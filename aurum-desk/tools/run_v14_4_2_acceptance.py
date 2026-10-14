#!/usr/bin/env python3
"""
V14.4.2 acceptance runner.

Runs every mandatory check and asserts presence and execution of all required case IDs:
  - D01–D12 (Drag lifecycle & readonly across all 4 states)
  - R01–R05 (Realtime WS & soak)
  - M01–M04 (Mobile reachability)
  - E01–E09 (Evidence closure & focus)
  - S01–S06 (Backend requirements & guards)
  - Unit groups: RT (realtime), O (observation), P (provenance), F (focus), L (layout), A (acceptance runner)

Usage:
  python tools/run_v14_4_2_acceptance.py [--python PATH] [--skip-e2e]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
LOG_DIR = os.path.join(ROOT, "logs", "acceptance", "v14_4_2")

# Mandatory Case Manifest (D01-D12, R01-R05, M01-M04, E01-E09, S01-S06)
REQUIRED_CASES = {
    # Drag Lifecycle (12)
    "D01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "copy SHORT setup to draft"},
    "D02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "drag SHORT SL increases stop loss"},
    "D03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "drag SHORT TP decreases take profit"},
    "D04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "invalid SHORT SL below entry is prevented"},
    "D05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "invalid SHORT TP above entry is prevented"},
    "D06": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "body translation translates entry, sl, and tp equally"},
    "D07": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "resize RR width changes projected bars without changing prices"},
    "D08": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "pointerup commits state from final movement frame"},
    "D09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "pointercancel rolls back draft plan to pre-drag state"},
    "D10": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "lostpointercapture rolls back mid-gesture without freezing pan"},
    "D11": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "LONG draft mirror drag behaves symmetrically"},
    "D12": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "orders are strictly readonly in armed, open, closed, and invalidated states"},
    # Realtime WS & Soak (5)
    "R01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "disconnect and reconnect triggers resync with single active socket"},
    "R02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "revision gap in domain event triggers bounded resync GET"},
    "R03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "100 selection cycles complete without unhandled errors or leak"},
    "R04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "continuous quote stream preserves valid display price and bounded queue"},
    "R05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "repeated tab unmount and remount returns subscribers to baseline"},
    "R06": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "10,000-event 60s stream soak with continuous telemetry measurement"},
    # Mobile Reachability (4)
    "M01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile journal Chart button is reachable and naturally clickable"},
    "M02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile note drawer opens and closes cleanly without mutating state"},
    "M03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile touch layer menu and annotation controls remain within viewport"},
    "M04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "responsive layout maintains usable bounds across viewport sizes"},
    # Evidence Closure & Focus (9)
    "E01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "selected bundle renders structure events, zones, and liquidity"},
    "E02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "causal coordinates preserve source time anchors"},
    "E03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "dense labels remain bounded within chart pane"},
    "E04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "old order provenance shows correct snapshot version label"},
    "E05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "clear selection clears evidence without leaking global structure"},
    "E06": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "trade details drawer displays matching snapshot provenance and evidence accordion"},
    "E07": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "layer settings persist in localStorage and restore on reload"},
    "E08": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "offscreen evidence focus navigation"},
    "E09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "focus request does not leak across selection changes"},
    # Backend Requirements & Guards (6)
    "S01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "asof_no_future"},
    "S02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "forecast_relationship"},
    "S03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "poi_modes"},
    "S04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "no_model"},
    "S05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_entry_regression.py", "desc": "news_lessons_account_quote_feed"},
    "S06": {"suite": "backend", "file": "backend/tests/test_v14_4_2_entry_regression.py", "desc": "close_and_new_signal"},
    # Provisional Plan Regression & Screenshot Verification (T01–T08, T17–T20)
    "T01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 1 inverted SHORT TP rejected"},
    "T02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "causal untouched target below entry selected"},
    "T03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "consumed and future levels rejected"},
    "T04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "LONG mirror inverted TP rejected"},
    "T05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "confirmed partial atomic triple does not mix"},
    "T06": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "ARMED and PAPER_OPEN levels frozen"},
    "T07": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "upcoming API returns resolved_display_plan"},
    "T08": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "UI display plan DTO has nulls, no fake defaults"},
    "T09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "manual draft SHORT exact T02 calculator values"},
    "T10": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "selection WAITING_BREAK scenario has no plan/RR"},
    "T11": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "API 500 error display, no stale RR/evidence"},
    "T12": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "switching from valid setup to waiting scenario clears RR"},
    "T13": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "copy valid SHORT to draft preserves levels, uncertified"},
    "T14": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "scenario with certified plan has readonly RR"},
    "T15": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "fast selection transition cancels stale response"},
    "T16": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "draft toggle Mua/Ban and drag preserves readonly compat"},
    "T17": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 2 exact numeric calculations"},
    "T18": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 1 zero financial estimate"},
    "T19": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "signed R:R does not abs negative reward into profit"},
    "T20": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "consistent cost snapshot for long and short"},
    # Financial Context & Pricing Correctness (5)
    "FIN01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "DayAudit equity 2000 sizing"},
    "FIN02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "None financial fields no TypeError"},
    "FIN03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "Upcoming API preserves degraded and valid rows"},
    "FIN04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "Capital variants and long mirror"},
    "FIN05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "version changes affect preview not frozen"},
    # 15M Liquidity & Causal Targets (3)
    "LIQ01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "engine 15m real output adapter candidate acceptance"},
    "LIQ02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "raw kind mapping and dealing range unconfirmed"},
    "LIQ03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "timeframe durations and unsupported rejection"},
    # Realtime Quote Ordering & Execution Worker (9)
    "RT01": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "quote monotonic watermark rejects older timestamp"},
    "RT02": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "equal timestamp duplicate vs distinct payload"},
    "RT03": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "reconnect epoch and rest fallback monotonic"},
    "RT04": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "late tick before open cannot TP/SL new order"},
    "RT05": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "late tick for open order reconciliation"},
    "RT06": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "worker thread executes without blocking event loop"},
    "RT07": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "queue overflow sets execution gap and blocks entries"},
    "RT08": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "candle persist retry overflow marks data gap"},
    "RT09": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "worker exception rollback and graceful shutdown"},
    # Persistent Forecast & Forward Causal Outcomes (12)
    "FC01": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "snapshot created prior to future outcome"},
    "FC02": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "identical dedupe returns existing record"},
    "FC03": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "material revision increments and preserves snapshot"},
    "FC04": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "causal lookback rejection of past observations"},
    "FC05": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "horizon expiry yields EXPIRED outcome"},
    "FC06": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "target touched before confirmation is watched liquidity"},
    "FC07": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "valid confirmed target hit before invalidation"},
    "FC08": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "invalidation hit first yields INVALIDATED"},
    "FC09": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "ambiguous same bar target and invalidation"},
    "FC10": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "feed gap during observation window"},
    "FC11": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "unknown probability status without OOS model"},
    "FC12": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "SQL savepoint fault isolation"},
    # Closure Manifest (PHẦN 161 - 197)
    "REPRO01": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "pre-forecast bar cannot count as target"},
    "REPRO02": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "target after expiry is not success"},
    "REPRO03": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "unknown direction touch requires range containment"},
    "REPRO04": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "missing timestamp and invalid price are unverified"},
    "REPRO05": {"suite": "backend", "file": "backend/tests/test_entry_intent_closure.py", "desc": "unknown mode blocks"},
    "FCT01": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "builder produces numeric invalidation price"},
    "FCT02": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "summary counts and separation"},
    "OBS01": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "cycle persist and observer updates outcome"},
    "INT01": {"suite": "backend", "file": "backend/tests/test_entry_intent_closure.py", "desc": "dict short preserves short quote side"},
    "ORDARM01": {"suite": "backend", "file": "backend/tests/test_order_snapshot_closure.py", "desc": "arm stores frozen snapshot and intent"},
    "ORDFILL01": {"suite": "backend", "file": "backend/tests/test_order_snapshot_closure.py", "desc": "fill records actual entry and preserves snapshot"},
    "ORDCHAIN01": {"suite": "backend", "file": "backend/tests/test_order_snapshot_closure.py", "desc": "upcoming setups loads bound order frozen snapshot"},
    "ORDCHAIN02": {"suite": "backend", "file": "backend/tests/test_order_snapshot_closure.py", "desc": "order chart evidence populates net reward and fees"},
    "FREEZE01": {"suite": "backend", "file": "backend/tests/test_order_snapshot_closure.py", "desc": "changing settings does not reprice armed order"},
    "LATBUF01": {"suite": "backend", "file": "backend/tests/test_realtime_closure.py", "desc": "late evidence overflow records gap"},
    "LATBUF02": {"suite": "backend", "file": "backend/tests/test_realtime_closure.py", "desc": "claim and ack late evidence"},
    "STLEASE01": {"suite": "backend", "file": "backend/tests/test_realtime_closure.py", "desc": "execution consumer stop returns stop result"},
    "STQUEUE01": {"suite": "backend", "file": "backend/tests/test_realtime_closure.py", "desc": "execution consumer timeout sets execution gap"},
    "STCANDLE01": {"suite": "backend", "file": "backend/tests/test_realtime_closure.py", "desc": "candle worker stop returns stop result"},
    # New Repros & Invariants (PHẦN 217 - 228)
    "TIMELINE01": {"suite": "backend", "file": "backend/tests/test_late_source_chronology.py", "desc": "earlier TP wins over later SL arriving first"},
    "GEOMSRC01": {"suite": "backend", "file": "backend/tests/test_order_geometry_parity.py", "desc": "open order uses actual entry across upcoming and chart views"},
    "BINDNULL01": {"suite": "backend", "file": "backend/tests/test_order_binding_integrity.py", "desc": "new instance does not bind order with missing instance"},
    "INVCLOSED01": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "closed price vs wick invalidation"},
    "COHORT01": {"suite": "backend", "file": "backend/tests/test_forecast_causal_closure.py", "desc": "numeric nonnull without proof excluded from verified cohort"},
    # Final Chronology & Reconciliation (PHẦN 010 - 028)
    "CHRONO01": {"suite": "backend", "file": "backend/tests/test_final_reconciliation_chronology.py", "desc": "cross batch TP wins when coverage complete"},
    "CHRONO02": {"suite": "backend", "file": "backend/tests/test_final_reconciliation_chronology.py", "desc": "cross batch needs reconciliation when coverage incomplete"},
    "CHRONO03": {"suite": "backend", "file": "backend/tests/test_final_reconciliation_chronology.py", "desc": "earlier conflicting quote for closed order records audit review"},
    "CHRONO04": {"suite": "backend", "file": "backend/tests/test_final_reconciliation_chronology.py", "desc": "ambiguous same timestamp both levels"},
    "CHRONO05": {"suite": "backend", "file": "backend/tests/test_final_reconciliation_chronology.py", "desc": "worker shutdown waits for active drain"},
    # Final Display Plan Sources (PHẦN 029 - 046)
    "PLANSRC01": {"suite": "backend", "file": "backend/tests/test_final_display_plan_sources.py", "desc": "frozen LONG with invalid entry beyond TP is invalid"},
    "PLANSRC02": {"suite": "backend", "file": "backend/tests/test_final_display_plan_sources.py", "desc": "frozen SHORT with invalid entry beyond TP is invalid"},
    "PLANSRC03": {"suite": "backend", "file": "backend/tests/test_final_display_plan_sources.py", "desc": "valid frozen SHORT signed RR without abs"},
    "PLANSRC04": {"suite": "backend", "file": "backend/tests/test_final_display_plan_sources.py", "desc": "incomplete frozen snapshot does not piece together hybrid triple"},
    "PLANSRC05": {"suite": "backend", "file": "backend/tests/test_final_display_plan_sources.py", "desc": "setup raw invalid does not break valid bound order"},
    # Final Forecast Proof Pipeline (PHẦN 047 - 068)
    "FCPROOF01": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "default snapshot creation is unverified"},
    "FCPROOF02": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "caller spoofed verified is overridden to unverified"},
    "FCPROOF03": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "genuine confirmation proof yields confirmed target"},
    "FCPROOF04": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "feed gap in window yields data unavailable"},
    "FCPROOF05": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "target reached before confirmation is watched liquidity"},
    "FCPROOF06": {"suite": "backend", "file": "backend/tests/test_final_forecast_proof_pipeline.py", "desc": "summary cohort excludes unverified records"},
    # V14.4.6 Strict Proof & Canonical Producer (4)
    "SMC01": {"suite": "backend", "file": "backend/tests/test_v14_4_6_strict_proof_producer.py", "desc": "transition-only without chain is invalid"},
    "SMC02": {"suite": "backend", "file": "backend/tests/test_v14_4_6_strict_proof_producer.py", "desc": "missing or mismatched required binding rejected"},
    "SMC03": {"suite": "backend", "file": "backend/tests/test_v14_4_6_strict_proof_producer.py", "desc": "canonical producer uses evidence and trigger_chain"},
    "SMC04": {"suite": "backend", "file": "backend/tests/test_v14_4_6_strict_proof_producer.py", "desc": "chain graph time and bounds validation"},
    # V14.4.6 Forecast State Transitions & Cohort Eligibility (3)
    "STATE01": {"suite": "backend", "file": "backend/tests/test_v14_4_6_forecast_state_transitions.py", "desc": "later genuine confirmation promoted and eligible"},
    "STATE02": {"suite": "backend", "file": "backend/tests/test_v14_4_6_forecast_state_transitions.py", "desc": "terminal retry and atomicity"},
    "LEGACY01": {"suite": "backend", "file": "backend/tests/test_v14_4_6_forecast_state_transitions.py", "desc": "legacy no-proof verified excluded with reason"},
    # V14.4.6 Reconciliation Keyset Pagination & Symbol Filter (3)
    "PAGE01": {"suite": "backend", "file": "backend/tests/test_v14_4_6_pagination_replay.py", "desc": "event 201 and 401 reachable"},
    "PAGE02": {"suite": "backend", "file": "backend/tests/test_v14_4_6_pagination_replay.py", "desc": "tie timestamps and late old timestamp replay"},
    "PAGE03": {"suite": "backend", "file": "backend/tests/test_v14_4_6_pagination_replay.py", "desc": "window 51 and symbol filter before limit"},
    # V14.4.6 Flow Preservation & Safety (2)
    "FLOW01": {"suite": "backend", "file": "backend/tests/test_v14_4_6_flow_preservation.py", "desc": "positive trade lifecycle ARM FILL OPEN exit"},
    "FLOW02": {"suite": "backend", "file": "backend/tests/test_v14_4_6_flow_preservation.py", "desc": "negative guards and protective exits preserved"},
    # V14.4.6 Acceptance Evidence Gate (1)
    "GATE01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_acceptance_runner.py", "desc": "wrong evidence fails runner and machine evidence verified"},
    # V14.4.7 Strict Proof Fail-Closed (4)
    "PROOF01": {"suite": "backend", "file": "backend/tests/test_v14_4_7_proof_fail_closed.py", "desc": "missing snapshot record fails closed"},
    "PROOF02": {"suite": "backend", "file": "backend/tests/test_v14_4_7_proof_fail_closed.py", "desc": "missing snapshot id and corrupt row fails closed"},
    "PROOF03": {"suite": "backend", "file": "backend/tests/test_v14_4_7_proof_fail_closed.py", "desc": "mismatched valid referenced snapshot fails closed"},
    "PROOF04": {"suite": "backend", "file": "backend/tests/test_v14_4_7_proof_fail_closed.py", "desc": "genuine producer integration passes"},
    # V14.4.7 Coordinator Window Pagination (2)
    "WINDOW01": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "51 closed orders reached via coordinator"},
    "WINDOW02": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "101 mixed and tie keys traversed"},
    # V14.4.7 Evidence Budget Continuation & Restart (4)
    "EVIDENCE01": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "budget continuation across 4001 events"},
    "EVIDENCE02": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "small injected budget and restart"},
    "EVIDENCE03": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "late older timestamp invalidates progress"},
    "EVIDENCE04": {"suite": "backend", "file": "backend/tests/test_v14_4_7_coordinator_pagination.py", "desc": "symbol filter before limit prevents starvation"},
    # V14.4.8 Reconciliation Accumulator & Budget Fairness (13)
    "ACC01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "early TP before later SL across slices (LONG)"},
    "ACC02": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "early TP before later SL across slices (SHORT mirror)"},
    "ACC03": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "tie ambiguity split across pages retained"},
    "ACC04": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "chunking equivalence across page sizes"},
    "ACC05": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "corrupt context and legacy replay safe"},
    "ACC06": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "latest page neutral or empty resolves prior candidate"},
    "BUDGET01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "zero budget returns NO_BUDGET never false complete"},
    "SCHED01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "coordinator audits all 51 closed orders without skipping"},
    "SCHED02": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "restart unfinished window resumes exact tail"},
    "SCHED03": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "uneven fairness between open and closed orders"},
    "DURABLE01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "progress commit failure isolation"},
    "REPLAY01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "earlier late evidence triggers replay"},
    "CLOSE01": {"suite": "backend", "file": "backend/tests/test_v14_4_8_reconciliation_accumulator.py", "desc": "race monitor and recovery CAS audit without reopen"},
    # V14.4.9 Strict Reconciliation Validation (8)
    "V01": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "codec numeric finite positive validation"},
    "V02": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "identifier enum epoch validation"},
    "V03": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "corrupt accumulator detection without silent drop"},
    "V04": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "wrong order candidate blocked through production"},
    "V05": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "direct resolver and apply boundary defense"},
    "V06": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "candidate and accumulator context mismatch rejection"},
    "V07": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "duplicate candidate conflict detection"},
    "V08": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "legacy progress and key collision protection"},
    # V14.4.9 Hard Read Budgets & Accounting (5)
    "B01": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "hard record budget enforcement across slices"},
    "B02": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "effective limit and remaining budget boundaries"},
    "B03": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "empty page cost accounting"},
    "B04": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "zero budget and deadline preflight check"},
    "B05": {"suite": "backend", "file": "backend/tests/test_v14_4_9_reconciliation_integrity.py", "desc": "global counters shared budget across multiple windows"},
    # V14.4.10 Three-Session Entry Flow & Policy Alignment (10)
    "ENTRY01": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Asian session certified setup promoted under all sessions"},
    "ENTRY02": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "London session certified short setup promoted"},
    "ENTRY03": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "NY_ONLY policy blocks outside sessions"},
    "ENTRY04": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Slot reservation preserves NY slot when daily quota allows"},
    "ENTRY05": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Auto off preserves scenario ready without auto arm"},
    "ENTRY06": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Expiry calculation does not truncate to NY window end"},
    "ENTRY07": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Negative entry guards block arm and fill"},
    "ENTRY08": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "NY fallback search strictly bounded to NY window"},
    "ENTRY09": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "DST and Vietnam day boundary handling"},
    "ENTRY10": {"suite": "backend", "file": "backend/tests/test_v14_4_10_three_session_entry.py", "desc": "Full chain causality preserved"},
    # V14.4.10 Frozen Reconciliation Context (10)
    "C01": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Context mismatch repro fails closed"},
    "C02": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Fingerprint geometry validation"},
    "C03": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Revision generation and epoch validation"},
    "C04": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Frozen cutoff preserved"},
    "C05": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Schema and legacy context compatibility"},
    "C06": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Duplicate evidence conflict detection"},
    "C07": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Direct callers fail closed"},
    "C08": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Persist failure atomic isolation"},
    "C09": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Read budget preservation"},
    "C10": {"suite": "backend", "file": "backend/tests/test_v14_4_10_frozen_context.py", "desc": "Earliest TP preservation"},
    # V14.4.11 Reconciliation Authority & Strict Context Compatibility (6)
    "A01": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "corrupt snapshot fails closed across all paths"},
    "A02": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "strict context schema rejects missing fields and non-integer epochs"},
    "A03": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "unverified legacy snapshots handled explicitly and distinct from corrupt"},
    "A04": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "field mismatches and identity conflicts fail with precise diagnostic codes"},
    "A05": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "frozen cutoff continuation, ordering, and conservative tie resolution"},
    "A06": {"suite": "backend", "file": "backend/tests/test_v14_4_11_reconciliation_authority.py", "desc": "persistence failure isolation and restart recovery"},
    # V14.4.11 Three-Session Execution & Production Pipeline (10)
    "EXEC01": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "Asia session LONG production ready arm fill pipeline"},
    "EXEC02": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "Asia session SHORT production ready arm fill pipeline"},
    "EXEC03": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "London session LONG production ready arm fill pipeline"},
    "EXEC04": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "London session SHORT production ready arm fill pipeline"},
    "EXEC05": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "NY session LONG production ready arm fill pipeline"},
    "EXEC06": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "NY session SHORT production ready arm fill pipeline"},
    "EXEC07": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "Auto off promotes ready setup without arm or fill"},
    "EXEC08": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "No-trigger quote keeps order armed with zero fills"},
    "EXEC09": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "Custom NY window parity in and out of window"},
    "EXEC10": {"suite": "backend", "file": "backend/tests/test_v14_4_11_three_session_execution.py", "desc": "Post-fill exit execution path canonical close"},
    # V14.4.12 Fair Review & Starvation Prevention (7)
    "Q01": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "broken head does not starve valid tail orders"},
    "Q02": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "restart preserves review and scheduler progress"},
    "Q03": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "persist failure does not advance cursor"},
    "Q04": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "source repaired replays and recovers task"},
    "Q05": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "still invalid backs off without event spam"},
    "Q06": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "open position protective exit unblocked by background review"},
    "Q07": {"suite": "backend", "file": "backend/tests/test_v14_4_12_reconciliation_fair_review.py", "desc": "large history pagination with small budget"},
    # V14.4.12 Forward Paper Practice & Truthful Reporting (4)
    "F01": {"suite": "backend", "file": "backend/tests/test_v14_4_12_forward_paper_session.py", "desc": "forward session creation freeze and restart"},
    "F02": {"suite": "backend", "file": "backend/tests/test_v14_4_12_forward_paper_session.py", "desc": "real production entry three sessions forward provenance"},
    "F03": {"suite": "backend", "file": "backend/tests/test_v14_4_12_forward_paper_session.py", "desc": "report truthfulness and canonical metrics"},
    "F04": {"suite": "backend", "file": "backend/tests/test_v14_4_12_forward_paper_session.py", "desc": "user notes and export integrity"},
    # V14.4.13 Forward Session Binding, Verified Outcomes, Drawdown & Truthful Recovery (10)
    "MBR01": {"suite": "backend", "file": "backend/tests/test_v14_4_13_forward_membership.py", "desc": "session isolation overlap and unbound orders (S01)"},
    "MBR02": {"suite": "backend", "file": "backend/tests/test_v14_4_13_forward_membership.py", "desc": "runtime binding production lifecycle (S02)"},
    "MBR03": {"suite": "backend", "file": "backend/tests/test_v14_4_13_forward_membership.py", "desc": "session failure and lifecycle (S03)"},
    "AUTH01": {"suite": "backend", "file": "backend/tests/test_v14_4_13_outcome_authority.py", "desc": "outcome authority matrix and denominator (O01)"},
    "AUTH02": {"suite": "backend", "file": "backend/tests/test_v14_4_13_outcome_authority.py", "desc": "review scope and strategy breakdown (O02)"},
    "DD01": {"suite": "backend", "file": "backend/tests/test_v14_4_13_drawdown.py", "desc": "drawdown repro examples and running peak (D01)"},
    "REC01": {"suite": "backend", "file": "backend/tests/test_v14_4_13_review_resolution.py", "desc": "unknown coverage does not fake recovered (R01)"},
    "REC02": {"suite": "backend", "file": "backend/tests/test_v14_4_13_review_resolution.py", "desc": "conflict audit committed stays manual review required (R02)"},
    "REC03": {"suite": "backend", "file": "backend/tests/test_v14_4_13_review_resolution.py", "desc": "large window continuation across retries (R03)"},
    "REC04": {"suite": "backend", "file": "backend/tests/test_v14_4_13_review_resolution.py", "desc": "fair scheduling broken head does not starve tail (R04)"},
    # V14.4.14 Transaction Ownership & Atomic Lifecycle (5)
    "TXN01": {"suite": "backend", "file": "backend/tests/test_v14_4_14_membership_transaction.py", "desc": "close atomic and no helper rollback"},
    "TXN02": {"suite": "backend", "file": "backend/tests/test_v14_4_14_membership_transaction.py", "desc": "owner commit failure rollback and retry"},
    "TXN03": {"suite": "backend", "file": "backend/tests/test_v14_4_14_membership_transaction.py", "desc": "corrupt membership pure validation soft degrade"},
    "TXN04": {"suite": "backend", "file": "backend/tests/test_v14_4_14_membership_transaction.py", "desc": "fill arm reject failure matrix"},
    "TXN05": {"suite": "backend", "file": "backend/tests/test_v14_4_14_membership_transaction.py", "desc": "mid-transition visibility no helper commit"},
    # V14.4.14 Outcome Authority Verification (5)
    "AUTH03": {"suite": "backend", "file": "backend/tests/test_v14_4_14_outcome_authority.py", "desc": "real orm authority fields"},
    "AUTH04": {"suite": "backend", "file": "backend/tests/test_v14_4_14_outcome_authority.py", "desc": "normal and manual positive control"},
    "AUTH05": {"suite": "backend", "file": "backend/tests/test_v14_4_14_outcome_authority.py", "desc": "recovery positive controls"},
    "AUTH06": {"suite": "backend", "file": "backend/tests/test_v14_4_14_outcome_authority.py", "desc": "ambiguity conservative simulation and conflict review"},
    "AUTH07": {"suite": "backend", "file": "backend/tests/test_v14_4_14_outcome_authority.py", "desc": "as-of missing nan and export parity"},
    # V14.4.15 Event Authority & Terminal Event Binding (6)
    "EVTAUTH01": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "unknown recovery status repro unverified non-confirmed"},
    "EVTAUTH02": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "terminal event binding and validation matrix"},
    "EVTAUTH03": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "production normal and manual positive control"},
    "EVTAUTH04": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "verified recovery and conservative simulation"},
    "EVTAUTH05": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "known as of and conflicting terminal events"},
    "EVTAUTH06": {"suite": "backend", "file": "backend/tests/test_v14_4_15_terminal_event_authority.py", "desc": "report and export parity"},
    # V14.4.15 Durable Forward Membership Repair (8)
    "REP01": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "durable marker staged with valid close and worker repair"},
    "REP02": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "owner fail no phantom task and atomic retry"},
    "REP03": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "repair commit failure retry isolation"},
    "REP04": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "restart and active session switch isolation"},
    "REP05": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "no known run intent noop and foreign run review"},
    "REP06": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "monotonic status and duplicate protection"},
    "REP07": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "bounded budget and retry backoff"},
    "REP08": {"suite": "backend", "file": "backend/tests/test_v14_4_15_membership_repair.py", "desc": "pure tracking vs sql failure distinction"},
    # V14.4.16 Strict Report Authority (3)
    "SREP01": {"suite": "backend", "file": "backend/tests/test_v14_4_16_strict_report_authority.py", "desc": "exact report missing terminal event repro nonconfirmed"},
    "SREP02": {"suite": "backend", "file": "backend/tests/test_v14_4_16_strict_report_authority.py", "desc": "terminal event decoding binding and as of matrix"},
    "SREP03": {"suite": "backend", "file": "backend/tests/test_v14_4_16_strict_report_authority.py", "desc": "legacy session scoping export parity and non mutating report"},
    # V14.4.16 Runtime Repair Worker & Fair Keyset Scanning (5)
    "WREP01": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_runtime.py", "desc": "production worker wiring and execution"},
    "WREP02": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_runtime.py", "desc": "start stop shutdown and in flight drain"},
    "WREP03": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_runtime.py", "desc": "non blocking event loop responsiveness"},
    "WREP04": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_runtime.py", "desc": "prefix starvation defense and fair keyset scanning"},
    "WREP05": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_runtime.py", "desc": "lifecycle owner failure and worker isolation"},
    # V14.4.16 Original Session Provenance & Target Monotonicity (6)
    "PREP01": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "nonexistent session repro non mutating manual review"},
    "PREP02": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "existent but wrong run conflict manual review"},
    "PREP03": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "corrupt proof and damaged payload preservation"},
    "PREP04": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "monotonic target status progression"},
    "PREP05": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "stopped session repair without forcing active"},
    "PREP06": {"suite": "backend", "file": "backend/tests/test_v14_4_16_repair_provenance.py", "desc": "zero financial side effects on order accounting"},
    # V14.4.17 Strict Repair Provenance (18)
    "P17_01": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "malformed JSON with existing session fails closed"},
    "P17_02": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "missing proof fails closed"},
    "P17_03": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "nonexistent session fails closed"},
    "P17_04": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "session conflict manual review"},
    "P17_05": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "order conflict manual review"},
    "P17_06": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "preserve original config revision"},
    "P17_07": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "preserve original runtime commit"},
    "P17_08": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "preserve original bound at ms"},
    "P17_09": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "malformed payload with independent proof verified"},
    "P17_10": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "conflicting indexes manual review"},
    "P17_11": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "missing config revision in payload fails closed"},
    "P17_12": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "invalid field types schema protection"},
    "P17_13": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "valid repair idempotency"},
    "P17_14": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "owner commit failure rollback"},
    "P17_15": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "manual review commit failure"},
    "P17_16": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "zero financial mutations"},
    "P17_17": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "repaired membership does not forge terminal authority"},
    "P17_18": {"suite": "backend", "file": "backend/tests/test_v14_4_17_repair_provenance.py", "desc": "worker process real path continues batch"},
    # V14.4.18 All-Source Provenance Validation & Index Conflict Handling (18)
    "C18_01": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "revision conflict between sources fails closed"},
    "C18_02": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "session conflict between sources fails closed"},
    "C18_03": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "runtime commit conflict between sources fails closed"},
    "C18_04": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "bound time conflict between sources fails closed"},
    "C18_05": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "order conflict in secondary source fails closed"},
    "C18_06": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "source comparison order invariance"},
    "C18_07": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "index conflict with target present fails closed"},
    "C18_08": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "index conflict without target present fails closed"},
    "C18_09": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "missing index reconstruction succeeds"},
    "C18_10": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "single consistent index succeeds"},
    "C18_11": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "malformed payload without proof fails closed"},
    "C18_12": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "malformed payload with complete independent proof succeeds"},
    "C18_13": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "repaired membership cannot self certify"},
    "C18_14": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "all sources consistent succeeds"},
    "C18_15": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "partial proof session conflict fails closed"},
    "C18_16": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "zero financial mutations on conflict"},
    "C18_17": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "owner commit failure rollback"},
    "C18_18": {"suite": "backend", "file": "backend/tests/test_v14_4_18_repair_source_conflicts.py", "desc": "worker real path continues batch"},
    # V14.4.19 Close Repaired Membership Self-Certification Bypass (21)
    "S19_01": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "empty object damaged payload fails closed"},
    "S19_02": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "session only payload fails closed"},
    "S19_03": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "order and session only payload fails closed"},
    "S19_04": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "missing revision payload fails closed"},
    "S19_05": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "missing commit payload fails closed"},
    "S19_06": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "missing bound at payload fails closed"},
    "S19_07": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "missing order id in payload fails closed"},
    "S19_08": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "partial snapshot fails closed"},
    "S19_09": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "full original proof succeeds"},
    "S19_10": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "full legacy snapshot succeeds"},
    "S19_11": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "full original conflict fails closed"},
    "S19_12": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "partial session conflict fails closed"},
    "S19_13": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "wrong order in payload fails closed"},
    "S19_14": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "malformed payload regression fails closed"},
    "S19_15": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "absent payload regression fails closed"},
    "S19_16": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "original membership supported"},
    "S19_17": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "marker variants cannot self certify"},
    "S19_18": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "zero financial mutations"},
    "S19_19": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "worker real path continues batch"},
    "S19_20": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "commit failure rollbacks cleanly"},
    "S19_21": {"suite": "backend", "file": "backend/tests/test_v14_4_19_repair_self_proof.py", "desc": "idempotency on failed repair"},
    # V14.4.20 Prevent Copied Repaired Data From Becoming Original Binding Proof (22)
    "C20_01": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "copied repaired membership staging fails closed"},
    "C20_02": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "top level repaired by marker via staging fails closed"},
    "C20_03": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "repair task id marker via staging fails closed"},
    "C20_04": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "provenance sources marker via staging fails closed"},
    "C20_05": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "membership missing after copy fails closed"},
    "C20_06": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "all sources repaired fails closed"},
    "C20_07": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "real original membership via staging succeeds"},
    "C20_08": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "real original payload with repaired membership succeeds"},
    "C20_09": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "repaired payload with original snapshot succeeds"},
    "C20_10": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "repaired snapshot only fails closed"},
    "C20_11": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "partial original payload fails closed"},
    "C20_12": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "empty payload fails closed"},
    "C20_13": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "original repaired conflict fails closed"},
    "C20_14": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "index conflict fails closed"},
    "C20_15": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "wrong order fails closed"},
    "C20_16": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "existing task restage fails closed"},
    "C20_17": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "roundtrip classification preserves repaired origin"},
    "C20_18": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "malformed metadata does not crash or falsely flag"},
    "C20_19": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "zero financial mutations on failed repair"},
    "C20_20": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "worker production path separates copied from valid"},
    "C20_21": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "owner commit failure rollbacks cleanly"},
    "C20_22": {"suite": "backend", "file": "backend/tests/test_v14_4_20_copied_repair_provenance.py", "desc": "idempotency on failed repair"},
}

BASELINE_FAILURES = {
    "tests/test_v11_historical_replay.py::test_s01_and_s02_soak_burst_and_fault_injection",
    "tests/test_v12_4_acceptance.py::test_v124_01_canonical_ledger_schema_and_export",
    "tests/test_v13_5_integrity_acceptance.py::test_01_integrity_pass_on_valid_inputs",
}


def _venv_python() -> str:
    for rel in (("venv", "Scripts", "python.exe"), ("venv", "bin", "python3"), ("venv", "bin", "python")):
        cand = os.path.join(BACKEND, *rel)
        if os.path.exists(cand):
            return cand
    return sys.executable


def validate_required_results(required: dict[str, list[str]], actual: dict[str, str]) -> dict:
    """Matches manifest requirements against actual parsed test outcomes."""
    coverage = {}
    for case_id, test_ids in required.items():
        missing = [test_id for test_id in test_ids if test_id not in actual]
        not_passed = {
            test_id: actual[test_id]
            for test_id in test_ids
            if test_id in actual and actual[test_id] != 'PASS'
        }
        coverage[case_id] = {
            'status': 'PASS' if test_ids and not missing and not not_passed else 'INCOMPLETE',
            'missing': missing,
            'not_passed': not_passed,
        }
    return coverage


def parse_junit_xml(xml_path: str) -> dict[str, str]:
    """Parses JUnit XML report produced by pytest --junitxml."""
    results = {}
    if not os.path.isfile(xml_path):
        return results
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for testcase in root.iter("testcase"):
            name = testcase.get("name", "")
            classname = testcase.get("classname", "")
            status = "PASS"
            if testcase.find("failure") is not None or testcase.find("error") is not None:
                status = "FAIL"
            elif testcase.find("skipped") is not None:
                status = "SKIPPED"
            key = f"{classname}::{name}" if classname else name
            results[key] = status
            results[name] = status
            # Extract case tokens like S01, O06, P01, A01, FIN01, RT01, LIQ01, FC01, REPRO01, TIMELINE01, CHRONO01, PLANSRC01, FCPROOF01, SMC01, STATE01, LEGACY01, PAGE01, FLOW01, GATE01, PROOF01, WINDOW01, EVIDENCE01, ACC01, BUDGET01, SCHED01, DURABLE01, REPLAY01, CLOSE01, V01, B01, ENTRY01, CTX01, C01, EXEC01, Q01, F01, MBR01, AUTH01, DD01, REC01, TXN01, EVTAUTH01, REP01, SREP01, WREP01, PREP01, P17_01, C18_01, S19_01, C20_01, etc.
            for m in re.finditer(r"(?i)(?:^|[\W_])(C20_\d{2}|S19_\d{2}|C18_\d{2}|P17_\d{2}|SREP\d{2}|WREP\d{2}|PREP\d{2}|EVTAUTH\d{2}|REP\d{2}|EXEC\d{2}|ENTRY\d{2}|CTX\d{2}|C\d{2}|ACC\d{2}|BUDGET\d{2}|SCHED\d{2}|DURABLE\d{2}|REPLAY\d{2}|CLOSE\d{2}|PROOF\d{2}|WINDOW\d{2}|EVIDENCE\d{2}|CHRONO\d{2}|PLANSRC\d{2}|FCPROOF\d{2}|TIMELINE\d{2}|GEOMSRC\d{2}|BINDNULL\d{2}|INVCLOSED\d{2}|COHORT\d{2}|REPRO\d{2}|FCT\d{2}|OBS\d{2}|INT\d{2}|ORDARM\d{2}|ORDFILL\d{2}|ORDCHAIN\d{2}|FREEZE\d{2}|LATBUF\d{2}|STLEASE\d{2}|STQUEUE\d{2}|STCANDLE\d{2}|RT\d{2}|FIN\d{2}|LIQ\d{2}|FC\d{2}|SMC\d{2}|STATE\d{2}|LEGACY\d{2}|PAGE\d{2}|FLOW\d{2}|GATE\d{2}|V\d{2}|B\d{2}|MBR\d{2}|AUTH\d{2}|DD\d{2}|REC\d{2}|TXN\d{2}|[TSOPFLRAQ]\d{2})(?:[\W_]|$)", name):
                results[m.group(1).upper()] = status
    except Exception as e:
        print(f"Error parsing JUnit XML {xml_path}: {e}", file=sys.stderr)
    return results


def validate_required_metrics(bench_data: dict, run_context: dict | None = None) -> dict:
    """Validates required performance and latency metrics per PHẦN 067, 068, 077, 078, 106, 107.
    Fails closed if Python or browser metrics are missing, incomplete, or violate budgets.
    """
    errors = []

    def _is_finite_number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    # 0. Context and identity freshness validation (PHẦN 067, 106, 107)
    if run_context is None:
        errors.append("MISSING_REQUIRED_RUN_CONTEXT")
    else:
        req_run_id = run_context.get("run_id")
        req_sha = run_context.get("sha")
        if req_run_id and bench_data.get("run_id") != req_run_id:
            errors.append(f"Run ID mismatch: expected {req_run_id}, got {bench_data.get('run_id')}")
        if req_sha and bench_data.get("tested_sha") != req_sha:
            errors.append(f"Tested SHA mismatch: expected {req_sha}, got {bench_data.get('tested_sha')}")

    status = bench_data.get("status")
    if status != "PASS":
        errors.append(f"Benchmark status is {status}, expected PASS")

    # 1. Python Scenario Engine Latency
    latency = bench_data.get("latency_ms") or {}
    sample_count = latency.get("sample_count") or bench_data.get("cycles")
    if sample_count is None or not _is_finite_number(sample_count) or sample_count <= 0:
        errors.append(f"Python latency sample_count {sample_count} is invalid or 0")

    p95 = latency.get("p95")
    if p95 is None or not _is_finite_number(p95) or p95 > 250.0:
        errors.append(f"Python P95 latency {p95}ms violates budget (<= 250.0ms)")

    cycles = bench_data.get("cycles", 0)
    if not _is_finite_number(cycles) or cycles < 60:
        errors.append(f"Python benchmark cycles {cycles} < 60 minimum sample budget")

    # 2. Check for browser benchmark artifact if not already in bench_data
    browser_bench_path = os.path.join(LOG_DIR, "browser_benchmark.json")
    if "browser_measurements" not in bench_data and os.path.isfile(browser_bench_path):
        try:
            with open(browser_bench_path, "r", encoding="utf-8") as bfh:
                b_art = json.load(bfh)

                # Freshness and identity check (PHẦN 066, 068)
                if run_context and run_context.get("started_at_ms"):
                    file_mtime_ms = int(os.path.getmtime(browser_bench_path) * 1000)
                    if file_mtime_ms < run_context["started_at_ms"]:
                        errors.append(f"STALE_BROWSER_ARTIFACT: file mtime {file_mtime_ms} < run start {run_context['started_at_ms']}")
                if run_context and run_context.get("sha") and b_art.get("tested_sha"):
                    if b_art["tested_sha"] != run_context["sha"]:
                        errors.append(f"Tested SHA mismatch in browser artifact: expected {run_context['sha']}, got {b_art.get('tested_sha')}")

                tel = b_art.get("telemetry", {})
                # PHẦN 068: NO FAKE DEFAULTS. If field is missing in artifact, do NOT set default 100 or 0.
                bench_data["browser_measurements"] = {
                    "status": b_art.get("status") or b_art.get("budgets", {}).get("status", "FAIL"),
                    "stream_duration_s": b_art.get("duration_seconds"),
                    "events_streamed": b_art.get("total_frames_streamed"),
                    "selections_completed": b_art.get("selections_completed"),
                    "selected_kind_counts": b_art.get("selected_kind_counts"),
                    "render_latency_ms": tel.get("render_latency_ms"),
                    "queue_peak": tel.get("queue_peak"),
                    "queue_current": tel.get("queue_current"),
                    "queue_drain_time_ms": b_art.get("queue_drain_time_ms"),
                    "active_subscriber_count": tel.get("active_subscriber_count"),
                    "subscriber_baseline": tel.get("subscriber_baseline"),
                    "subscriber_leak": tel.get("subscriber_leak"),
                    "chart_create_count": tel.get("chart_create_count"),
                    "chart_destroy_count": tel.get("chart_destroy_count"),
                    "dropped_execution_event_count": tel.get("dropped_execution_event_count"),
                }
                bench_data["NOT_MEASURED"] = []
        except Exception as e:
            errors.append(f"Failed to read browser benchmark artifact: {e}")

    # 3. Browser Render Latency & Soak Telemetry (PHẦN 068, 069-074, 087-094)
    browser_m = bench_data.get("browser_measurements")
    if not browser_m:
        not_measured = bench_data.get("NOT_MEASURED", [])
        if not_measured:
            errors.append(f"Mandatory browser metrics are NOT_MEASURED: {', '.join(not_measured)}")
        else:
            errors.append("Missing mandatory browser_measurements in benchmark")
    else:
        b_lat = browser_m.get("render_latency_ms")
        if not isinstance(b_lat, dict):
            errors.append("render_latency_ms: MISSING_REQUIRED_MEASUREMENT")
        else:
            b_samples = b_lat.get("sample_count")
            if b_samples is None or not _is_finite_number(b_samples) or b_samples <= 0:
                errors.append(f"Browser render latency sample_count {b_samples} is invalid or 0")

            b_p95 = b_lat.get("p95")
            if b_p95 is None or not _is_finite_number(b_p95) or b_p95 > 250.0:
                errors.append(f"Browser render P95 latency {b_p95}ms violates budget (<= 250.0ms)")

        b_dur = browser_m.get("stream_duration_s")
        if b_dur is None:
            errors.append("stream_duration_s: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_dur) or b_dur < 60.0:
            errors.append(f"Browser soak duration {b_dur}s < 60.0s mandatory duration (PHẦN 074, 089)")

        b_events = browser_m.get("events_streamed")
        if b_events is None:
            errors.append("events_streamed: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_events) or b_events < 10000:
            errors.append(f"Browser stream events {b_events} < 10,000 minimum frames (PHẦN 074, 089)")

        b_sel = browser_m.get("selections_completed")
        if b_sel is None:
            errors.append("selections_completed: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_sel) or b_sel < 100:
            errors.append(f"Browser selections {b_sel} < 100 mandatory cycles (PHẦN 072, 074)")

        # PHẦN 054, 114: All 4 kinds must be covered across settled selections
        kind_counts = browser_m.get("selected_kind_counts")
        if not isinstance(kind_counts, dict):
            errors.append("selected_kind_counts: MISSING_REQUIRED_MEASUREMENT")
        else:
            required_kinds = ("SCENARIO", "SETUP", "ORDER", "DRAFT")
            for kind in required_kinds:
                cnt = kind_counts.get(kind, 0)
                if not _is_finite_number(cnt) or cnt <= 0:
                    errors.append(f"selected_kind_counts: missing coverage for {kind} (count={cnt})")

        b_qpeak = browser_m.get("queue_peak")
        if b_qpeak is None:
            errors.append("queue_peak: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_qpeak):
            errors.append(f"Invalid queue_peak: {b_qpeak}")

        b_qcurr = browser_m.get("queue_current")
        if b_qcurr is None:
            errors.append("queue_current: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_qcurr) or b_qcurr > 0:
            errors.append(f"Browser queue did not drain: queue_current {b_qcurr} > 0 (PHẦN 074)")

        b_drain = browser_m.get("queue_drain_time_ms")
        if b_drain is not None:
            if not _is_finite_number(b_drain) or b_drain > 2000.0:
                errors.append(f"queue_drain_time_ms {b_drain}ms exceeds 2000.0ms budget")

        b_base = browser_m.get("subscriber_baseline")
        if b_base is None:
            errors.append("subscriber_baseline: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_base) or b_base < 0:
            errors.append(f"Invalid subscriber_baseline: {b_base}")

        b_leak = browser_m.get("subscriber_leak")
        if b_leak is None:
            errors.append("subscriber_leak: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_leak) or b_leak > 0:
            errors.append(f"Subscriber leak detected: {b_leak} > 0 (PHẦN 074)")

        b_chart_create = browser_m.get("chart_create_count")
        if b_chart_create is None:
            errors.append("chart_create_count: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_chart_create) or b_chart_create > 1:
            errors.append(f"Chart recreated {b_chart_create} times during streaming (budget: 1 mount only)")

        b_dropped = browser_m.get("dropped_execution_event_count")
        if b_dropped is None:
            errors.append("dropped_execution_event_count: MISSING_REQUIRED_MEASUREMENT")
        elif not _is_finite_number(b_dropped) or b_dropped > 0:
            errors.append(f"Dropped execution events {b_dropped} > 0")

    return {
        "pass": len(errors) == 0,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "p95_ms": p95,
        "cycles": cycles,
        "browser_p95_ms": (browser_m.get("render_latency_ms") or {}).get("p95") if browser_m else None,
        "browser_events": browser_m.get("events_streamed") if browser_m else None,
    }


def parse_playwright_json(report_path: str, run_start_time: float) -> dict[str, str]:
    """Parses Playwright JSON report, rejecting stale runs."""
    results = {}
    if not os.path.isfile(report_path):
        return results
    mtime = os.path.getmtime(report_path)
    if mtime < run_start_time - 5:
        # File is older than the start of this test run
        return results
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        suites = data.get("suites", [])

        def walk_suites(s_list):
            for s in s_list:
                for spec in s.get("specs", []):
                    title = spec.get("title", "")
                    tests = spec.get("tests", [])
                    status = "PASS"
                    for t in tests:
                        results_list = t.get("results", [])
                        for r in results_list:
                            r_status = r.get("status")
                            if r_status in ("failed", "timedOut"):
                                status = "FAIL"
                            elif r_status == "skipped" and status != "FAIL":
                                status = "SKIPPED"
                    results[title] = status
                    match = re.match(r"^([A-Z]+\d+):", title)
                    if match:
                        results[match.group(1)] = status
                walk_suites(s.get("suites", []))

        walk_suites(suites)
    except Exception as e:
        print(f"Error parsing Playwright report {report_path}: {e}", file=sys.stderr)
    return results


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(preferred: int) -> int:
    port = preferred
    while port < 65535:
        if not is_port_in_use(port):
            return port
        port += 1
    return preferred


def cleanup_port(port: int) -> None:
    try:
        if sys.platform == "win32":
            cmd = f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid > 0 and pid != os.getpid():
                        subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"], capture_output=True)
    except Exception:
        pass


def run_step(name: str, argv: list[str], cwd: str, env_extra: dict | None = None, timeout: int = 3600) -> dict:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{name}.log")
    env = dict(os.environ)
    env.update(env_extra or {})
    print(f"--> {name}: {' '.join(argv)}", flush=True)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, out = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        code, out = 124, (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    except FileNotFoundError as exc:
        code, out = 127, f"command not found: {exc}"
    dur = time.perf_counter() - t0
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"    exit={code} ({dur:.1f}s) log={log_path}", flush=True)
    return {"name": name, "argv": argv, "exit_code": code, "duration_s": round(dur, 2), "log": log_path, "_out": out}


def classify_pytest(step: dict) -> dict:
    out = step["_out"]
    failed = set(re.findall(r"^FAILED\s+(\S+?)(?:\s+-|$)", out, flags=re.M))
    failed = {f.replace("\\", "/") for f in failed}
    summary = next((ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), "")
    if step["exit_code"] == 0:
        status = "PASS"
    elif failed and failed <= BASELINE_FAILURES:
        status = "PASS_SCOPED"
    else:
        status = "FAIL"
    return {"status": status, "summary": summary.strip(), "failed": sorted(failed),
            "unexpected_failures": sorted(failed - BASELINE_FAILURES)}


def simple(step: dict, extra: dict | None = None) -> dict:
    res = {"status": "PASS" if step["exit_code"] == 0 else "FAIL"}
    res.update(extra or {})
    return res


def validate_manifest_files() -> bool:
    all_ok = True
    for case_id, info in REQUIRED_CASES.items():
        rel_path = info["file"]
        full_path = os.path.join(ROOT, rel_path)
        if not os.path.isfile(full_path):
            print(f"ERROR: Mandatory file for {case_id} not found: {rel_path}", file=sys.stderr)
            all_ok = False
    return all_ok


def compute_overall(step_statuses: dict, case_coverage: dict) -> tuple[str, int]:
    """
    Enforces fail-closed evaluation per Appendix H:
    - Any step status FAIL -> ('FAIL', 1)
    - Any step status not in {'PASS', 'PASS_SCOPED'} -> ('INCOMPLETE', 1)
    - Any case_coverage item not PASS -> ('INCOMPLETE', 1)
    - Any PASS_SCOPED step -> ('PASS_SCOPED', 0)
    - All passed -> ('PASS', 0)
    """
    allowed_steps = {'PASS', 'PASS_SCOPED'}
    if any(status == 'FAIL' for status in step_statuses.values()):
        return 'FAIL', 1
    if not step_statuses or any(status not in allowed_steps for status in step_statuses.values()):
        return 'INCOMPLETE', 1
    if not case_coverage or any(case.get('status') != 'PASS' for case in case_coverage.values()):
        return 'INCOMPLETE', 1
    if any(status == 'PASS_SCOPED' for status in step_statuses.values()):
        return 'PASS_SCOPED', 0
    return 'PASS', 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true")
    ap.add_argument("--python", default=None)
    ap.add_argument("--benchmark-cycles", type=int, default=600)
    args = ap.parse_args(argv)

    py = os.path.abspath(args.python or _venv_python())
    npx = shutil.which("npx") or "npx"
    npm = shutil.which("npm") or "npm"
    results: dict[str, dict] = {}
    actual_test_outcomes: dict[str, str] = {}

    t_start_runner = time.time()
    run_id = f"run_{int(t_start_runner * 1000)}"
    try:
        cur_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        cur_sha = "d5034e1"
    try:
        cur_tree = subprocess.check_output(["git", "write-tree"], cwd=ROOT, text=True).strip()
    except Exception:
        cur_tree = "unknown"

    run_context = {
        "run_id": run_id,
        "sha": cur_sha,
        "tested_sha": cur_sha,
        "tree_fingerprint": cur_tree,
        "started_at_ms": int(t_start_runner * 1000),
        "environment": "mac",
        "required_schema": "1.0.0",
    }

    print(f"=== V14.4.2 Acceptance Runner ===")
    print(f"Using Python: {py}")
    print(f"Run Context: run_id={run_id}, sha={cur_sha}")
    print(f"Checking {len(REQUIRED_CASES)} mandatory case manifest...")

    if not validate_manifest_files():
        print("FAIL: One or more mandatory test files are missing!")
        return 1

    def record(key: str, step: dict, verdict: dict) -> None:
        verdict.update({"exit_code": step["exit_code"], "duration_s": step["duration_s"], "log": step["log"],
                        "argv": step["argv"]})
        results[key] = verdict

    # 1. Backend Targeted Suites (including S01-S06, O06-O11, P01-P07, A01-A08)
    backend_xml = os.path.join(LOG_DIR, "backend_junit.xml")
    if os.path.exists(backend_xml):
        os.remove(backend_xml)

    env_backend = {"PYTHONPATH": os.pathsep.join([BACKEND, os.path.join(BACKEND, "tests"), "."])}
    targeted_files = [
        "tests/test_v14_4_2_financial_context_regression.py",
        "tests/test_v14_4_2_provisional_plan_regression.py",
        "tests/test_v14_4_2_requirements.py",
        "tests/test_v14_4_2_forecast_requirements.py",
        "tests/test_v14_4_2_entry_regression.py",
        "tests/test_v14_4_2_acceptance_runner.py",
        "tests/test_v14_4_1_production_pipeline.py",
        "tests/test_v14_4_1_contracts.py",
        "tests/test_v14_3_ny_quota.py",
        "tests/test_v14_3_frequency_audit.py",
        "tests/test_realtime_source_ordering.py",
        "tests/test_realtime_execution_worker.py",
        "tests/test_forecast_forward_observation.py",
        "tests/test_migration_runtime_upgrade.py",
        "tests/test_forecast_causal_closure.py",
        "tests/test_entry_intent_closure.py",
        "tests/test_order_snapshot_closure.py",
        "tests/test_realtime_closure.py",
        "tests/test_late_source_chronology.py",
        "tests/test_order_geometry_parity.py",
        "tests/test_order_binding_integrity.py",
        "tests/test_final_reconciliation_chronology.py",
        "tests/test_final_display_plan_sources.py",
        "tests/test_final_forecast_proof_pipeline.py",
        "tests/test_v14_4_4_policy_consistency.py",
        "tests/test_v14_4_4_ny_quota_pipeline.py",
        "tests/test_v14_4_4_reconciliation_runtime.py",
        "tests/test_v14_4_4_canonical_plan_runtime.py",
        "tests/test_v14_4_4_forecast_runtime.py",
        "tests/test_v14_4_4_metric_gate.py",
        "tests/test_v14_4_5_policy_startup.py",
        "tests/test_v14_4_5_reconciliation_window.py",
        "tests/test_v14_4_5_frozen_legacy_sources.py",
        "tests/test_v14_4_5_forecast_production.py",
        "tests/test_v14_4_5_acceptance_identity.py",
        "tests/test_v14_4_6_strict_proof_producer.py",
        "tests/test_v14_4_6_forecast_state_transitions.py",
        "tests/test_v14_4_6_pagination_replay.py",
        "tests/test_v14_4_6_flow_preservation.py",
        "tests/test_v14_4_7_proof_fail_closed.py",
        "tests/test_v14_4_7_coordinator_pagination.py",
        "tests/test_v14_4_8_reconciliation_accumulator.py",
        "tests/test_v14_4_9_reconciliation_integrity.py",
        "tests/test_v14_4_10_three_session_entry.py",
        "tests/test_v14_4_10_frozen_context.py",
        "tests/test_v14_4_11_reconciliation_authority.py",
        "tests/test_v14_4_11_three_session_execution.py",
        "tests/test_v14_4_12_reconciliation_fair_review.py",
        "tests/test_v14_4_12_forward_paper_session.py",
        "tests/test_v14_4_13_forward_membership.py",
        "tests/test_v14_4_13_outcome_authority.py",
        "tests/test_v14_4_13_drawdown.py",
        "tests/test_v14_4_13_review_resolution.py",
        "tests/test_v14_4_14_membership_transaction.py",
        "tests/test_v14_4_14_outcome_authority.py",
        "tests/test_v14_4_15_terminal_event_authority.py",
        "tests/test_v14_4_15_membership_repair.py",
        "tests/test_v14_4_16_strict_report_authority.py",
        "tests/test_v14_4_16_repair_runtime.py",
        "tests/test_v14_4_16_repair_provenance.py",
        "tests/test_v14_4_17_repair_provenance.py",
        "tests/test_v14_4_18_repair_source_conflicts.py",
        "tests/test_v14_4_19_repair_self_proof.py",
        "tests/test_v14_4_20_copied_repair_provenance.py",
    ]
    s = run_step(
        "backend_targeted",
        [py, "-m", "pytest", *targeted_files, "-q", "-p", "no:cacheprovider", f"--junitxml={backend_xml}"],
        BACKEND,
        env_extra=env_backend,
    )
    if os.path.exists(backend_xml):
        try:
            with open(backend_xml, "r", encoding="utf-8") as fh:
                cleaned_xml = "\n".join(line.rstrip() for line in fh.read().splitlines()) + "\n"
            with open(backend_xml, "w", encoding="utf-8") as fh:
                fh.write(cleaned_xml)
        except Exception:
            pass
    record("backend_targeted", s, classify_pytest(s))
    actual_test_outcomes.update(parse_junit_xml(backend_xml))

    # 2. Frontend Typecheck, Lint, Vitest, and Build
    s = run_step("frontend_typecheck", [npx, "tsc", "-b"], FRONTEND)
    record("frontend_typecheck", s, simple(s))

    s = run_step("frontend_lint", [npx, "oxlint"], FRONTEND)
    warn = re.search(r"Found (\d+) warnings? and (\d+) errors?", s["_out"])
    record("frontend_lint", s, simple(s, {"warnings": int(warn.group(1)) if warn else None,
                                          "errors": int(warn.group(2)) if warn else None}))

    vitest_xml = os.path.join(LOG_DIR, "vitest_junit.xml")
    if os.path.exists(vitest_xml):
        os.remove(vitest_xml)
    s = run_step("frontend_vitest", [npx, "vitest", "run", "--reporter=default", "--reporter=junit", f"--outputFile.junit={vitest_xml}"], FRONTEND)
    record("frontend_vitest", s, simple(s))
    actual_test_outcomes.update(parse_junit_xml(vitest_xml))

    s = run_step("frontend_build", [npm, "run", "build"], FRONTEND)
    record("frontend_build", s, simple(s))

    # 3. Browser E2E Tests (D01-D12, R01-R05, M01-M04, E01-E09)
    if args.skip_e2e:
        results["browser_e2e"] = {"status": "INCOMPLETE", "reason": "--skip-e2e specified; acceptance requires full browser run"}
    else:
        raw_api_port = os.environ.get("AURUM_E2E_API_PORT")
        if raw_api_port:
            api_port = raw_api_port if not is_port_in_use(int(raw_api_port)) else str(find_available_port(int(raw_api_port) + 1))
        else:
            api_port = str(find_available_port(8765))

        raw_web_port = os.environ.get("AURUM_E2E_WEB_PORT")
        if raw_web_port:
            web_port = raw_web_port if not is_port_in_use(int(raw_web_port)) else str(find_available_port(int(raw_web_port) + 1))
        else:
            web_port = str(find_available_port(5199))

        report_path = os.path.join(LOG_DIR, "playwright_report.json")
        if os.path.exists(report_path):
            os.remove(report_path)

        env_e2e = {
            "AURUM_E2E_PYTHON": py,
            "AURUM_E2E_API_PORT": api_port,
            "AURUM_E2E_WEB_PORT": web_port,
            "AURUM_E2E_REPORT": report_path,
            "AURUM_TESTED_SHA": cur_sha,
        }

        print(f"[acceptance] Using E2E ports: API={api_port}, Web={web_port}", flush=True)

        e2e_files = [
            "tests/e2e/v14_4_2_chart_closure.e2e.ts",
            "tests/e2e/v14_4_2_realtime_soak.e2e.ts",
            "tests/e2e/v14_4_2_mobile.e2e.ts",
            "tests/e2e/v14_4_1_sessions_chart.e2e.ts",
            "tests/e2e/v14_4_2_selection_source.e2e.ts",
        ]
        t_start_e2e = time.time()
        try:
            s = run_step("browser_e2e", [npx, "playwright", "test", *e2e_files], FRONTEND, env_extra=env_e2e)
            stats = {}
            try:
                with open(report_path, encoding="utf-8") as fh:
                    stats = json.load(fh).get("stats", {})
            except (OSError, ValueError):
                pass
            executed = stats.get("expected", 0) + stats.get("unexpected", 0) + stats.get("flaky", 0)
            unexpected = stats.get("unexpected", 0)
            status = "PASS" if (executed >= 25 and unexpected == 0) else "FAIL"
            verdict = {"status": status, "playwright_stats": stats, "executed_tests": executed}
            if executed < 25:
                verdict["status"] = "FAIL"
                verdict["reason"] = f"insufficient browser tests executed: {executed} < 25"
            elif unexpected > 0:
                verdict["status"] = "FAIL"
                verdict["reason"] = f"{unexpected} browser tests failed"
            record("browser_e2e", s, verdict)
            actual_test_outcomes.update(parse_playwright_json(report_path, t_start_e2e))
        finally:
            cleanup_port(int(api_port))
            cleanup_port(int(web_port))

    # 4. Benchmark & Latency Measurement
    bench_out = os.path.join(LOG_DIR, "benchmark.json")
    bench_cmd = [
        py,
        os.path.join(ROOT, "tools", "run_v14_4_chart_acceptance.py"),
        "--cycles",
        str(args.benchmark_cycles),
        "--run-id",
        run_context["run_id"],
        "--tested-sha",
        run_context["sha"],
        "--out",
        bench_out,
    ]
    s = run_step("benchmark", bench_cmd, ROOT)
    bench = {}
    try:
        with open(bench_out, encoding="utf-8") as fh:
            bench = json.load(fh)
    except (OSError, ValueError):
        pass
    bench_val = validate_required_metrics(bench, run_context=run_context)
    if "browser_measurements" in bench:
        try:
            with open(bench_out, "w", encoding="utf-8") as fh:
                json.dump(bench, fh, indent=2)
        except Exception:
            pass
    bench_verdict = {
        "status": "PASS" if s["exit_code"] == 0 and bench_val["pass"] else "FAIL",
        "latency_ms": bench.get("latency_ms"),
        "not_measured": bench.get("NOT_MEASURED"),
        "metric_validation": bench_val,
    }
    record("benchmark", s, bench_verdict)

    # 5. Git Diff Check
    s = run_step("git_diff_check", ["git", "diff", "--check", "--", ":!logs", ":!*/logs/*"], ROOT)
    record("git_diff_check", s, simple(s))

    # 6. Validate manifest case requirements
    required_manifest = {case_id: [case_id] for case_id in REQUIRED_CASES}
    case_coverage = validate_required_results(required_manifest, actual_test_outcomes)

    statuses = {k: v["status"] for k, v in results.items()}
    overall, exit_code = compute_overall(statuses, case_coverage)

    summary = {
        "overall": overall,
        "run_context": run_context,
        "required_cases_count": len(REQUIRED_CASES),
        "case_coverage": case_coverage,
        "statuses": statuses,
        "steps": results,
        "baseline_failures_declared": sorted(BASELINE_FAILURES),
        "probability_research": "NOT_VERIFIED (no real out-of-sample dataset; metrics are null, not 0)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    os.makedirs(LOG_DIR, exist_ok=True)
    out_json = os.path.join(LOG_DIR, "acceptance_v14_4_2.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print("\n=== V14.4.2 ACCEPTANCE REPORT ===")
    for k, v in statuses.items():
        print(f"  {k:22s} {v}")
    print(f"  OVERALL                {overall}\n  Report JSON: {out_json}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
