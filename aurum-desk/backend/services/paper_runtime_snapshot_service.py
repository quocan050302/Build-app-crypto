"""
backend/services/paper_runtime_snapshot_service.py
V16.39.10 — Forward Paper Runtime Observation & Read-Only Snapshot Service

Strictly Read-Only (Rules 001-145):
- Never mutates database rows (zero INSERT/UPDATE/DELETE/REPLACE/CREATE/ALTER/DROP).
- Zero side-effects: no creating TradingPolicy, DayAudit, PaperResearchRun, or Checkpoint.
- Canonical Schema Version 2.0 with consistent semantics across HTTP and Offline modes.
- True 3-state Auto authority (True / False / None).
- Offline worker status is UNKNOWN with running=None (null in JSON).
- Truthful financial metrics: sample-verified accounting, expectancy 0.00 on breakeven, N/A on zero closed trades.
- Redaction of credentials and finite metric validation (no NaN / Infinity).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

import models
from services.ny_quota_helpers import classify_reason
from services.paper_research_run_service import calculate_running_peak_drawdown

logger = logging.getLogger("paper_runtime_snapshot_service")

SCHEMA_VERSION = "2.0"
DEFAULT_SYMBOL = "XAUUSDT"
DEFAULT_SCOPE = "CG01"

# Sensitive keywords pattern for sanitizing error strings or debug payloads
SENSITIVE_PATTERNS = [
    re.compile(r"(bot[0-9]+:[a-zA-Z0-9_-]{20,})", re.IGNORECASE),
    re.compile(r"([a-f0-9]{32,64})", re.IGNORECASE),
    re.compile(r"(password|secret|token|api_key|passphrase)\s*[:=]\s*['\"]?([^'\"\s&]+)", re.IGNORECASE),
    re.compile(r"(https?://)([^:]+):([^@]+)@", re.IGNORECASE),
]


def redact_sensitive_text(content: str) -> str:
    """Cleanses secrets, credentials, tokens, and passphrases from text."""
    if not isinstance(content, str):
        return content
    cleansed = content
    # Redact URL credentials: http://user:pass@host -> http://***:***@host
    cleansed = re.sub(r"(https?://)([^:]+):([^@]+)@", r"\1***:***@", cleansed)
    # Redact explicit token / key patterns
    for p in SENSITIVE_PATTERNS:
        cleansed = p.sub(r"***REDACTED***", cleansed)
    return cleansed


def get_git_commit(project_root: Optional[Path] = None) -> str:
    """Retrieves current git commit hash safely without throwing."""
    root = project_root or Path(__file__).resolve().parent.parent.parent
    try:
        res = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return res
    except Exception:
        return "UNKNOWN_COMMIT"


def make_snapshot_context(
    *,
    symbol: str = DEFAULT_SYMBOL,
    scope: str = DEFAULT_SCOPE,
    requested_run_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rule 023, 024, 025: Validates input parameters deterministically and locks a single now_ms.
    Does not call time.time() in multiple places to avoid sliding windows.
    """
    fixed_now = int(now_ms if now_ms is not None else (time.time() * 1000))

    if start_ms is not None and not isinstance(start_ms, int):
        try:
            start_ms = int(start_ms)
        except (ValueError, TypeError):
            raise ValueError(f"start_ms must be an integer epoch timestamp, got: {start_ms}")

    if end_ms is not None and not isinstance(end_ms, int):
        try:
            end_ms = int(end_ms)
        except (ValueError, TypeError):
            raise ValueError(f"end_ms must be an integer epoch timestamp, got: {end_ms}")

    if start_ms is not None and end_ms is not None:
        if end_ms <= start_ms:
            raise ValueError(f"end_ms ({end_ms}) must be strictly greater than start_ms ({start_ms})")

    effective_end = end_ms if end_ms is not None else fixed_now

    return {
        "symbol": str(symbol or DEFAULT_SYMBOL).strip().upper(),
        "scope": str(scope or DEFAULT_SCOPE).strip().upper(),
        "requested_run_id": str(requested_run_id).strip() if requested_run_id else None,
        "start_ms": start_ms,
        "end_ms": effective_end,
        "now_ms": fixed_now,
    }


def read_existing_policy_config(db: Session) -> Dict[str, Any]:
    """
    Rules 041-045: Read-only extraction of existing TradingPolicy and allowlisted SystemConfig.
    Strictly forbids get_or_create_policy, resolve_effective_daily_policy, or any commits.
    """
    policy_row = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()

    allowlist_keys = [
        "daily_max_cap",
        "daily_min_goal",
        "daily_ny_goal",
        "reserve_ny_slot",
        "scalp_m1_mode",
        "cg01_enabled",
        "cg01_profile_mode",
        "execution_profile",
        "timing_profile",
        "min_net_rr",
        "quality_scoring_enabled",
        "quality_arm_threshold",
        "lesson_score_adjustment_enabled",
    ]
    sys_configs = db.query(models.SystemConfig).filter(models.SystemConfig.key.in_(allowlist_keys)).all()
    cfg_map = {row.key: row.value for row in sys_configs}

    policy_data: Dict[str, Any] = {
        "present": policy_row is not None,
        "id": "default",
        "max_daily_fills": policy_row.max_daily_fills if policy_row else None,
        "daily_min_goal": getattr(policy_row, "daily_min_goal", None) if policy_row else None,
        "ny_min_fills": policy_row.ny_min_fills if policy_row else None,
        "reserve_ny_slot": policy_row.reserve_ny_slot if policy_row else None,
        "daily_timezone": getattr(policy_row, "daily_timezone", "America/New_York") if policy_row else None,
        "entry_session_policy": getattr(policy_row, "entry_session_policy", None) if policy_row else None,
        "is_active": policy_row.is_active if policy_row else None,
        "version": policy_row.version if policy_row else None,
    }

    # Safe scalp config retrieval without mutation
    from services.scalp_m1_types import load_effective_scalp_config
    scalp_cfg = load_effective_scalp_config(db)

    effective_config = {
        "policy_present": policy_row is not None,
        "policy": policy_data,
        "daily_max_cap": policy_data["max_daily_fills"] if policy_data["max_daily_fills"] is not None else (
            int(cfg_map["daily_max_cap"]) if "daily_max_cap" in cfg_map and cfg_map["daily_max_cap"] is not None else None
        ),
        "daily_min_goal": policy_data["daily_min_goal"] if policy_data["daily_min_goal"] is not None else (
            int(cfg_map["daily_min_goal"]) if "daily_min_goal" in cfg_map and cfg_map["daily_min_goal"] is not None else None
        ),
        "ny_min_fills": policy_data["ny_min_fills"] if policy_data["ny_min_fills"] is not None else (
            int(cfg_map["daily_ny_goal"]) if "daily_ny_goal" in cfg_map and cfg_map["daily_ny_goal"] is not None else None
        ),
        "reserve_ny_slot": policy_data["reserve_ny_slot"] if policy_data["reserve_ny_slot"] is not None else (
            cfg_map.get("reserve_ny_slot") == "true" if "reserve_ny_slot" in cfg_map else None
        ),
        "execution_profile": getattr(scalp_cfg, "execution_profile", cfg_map.get("execution_profile")),
        "cg01_enabled": getattr(scalp_cfg, "cg01_enabled", cfg_map.get("cg01_enabled") == "true" if "cg01_enabled" in cfg_map else None),
        "cg01_profile_mode": getattr(scalp_cfg, "cg01_profile_mode", cfg_map.get("cg01_profile_mode")),
        "timing_profile": getattr(scalp_cfg, "timing_profile", cfg_map.get("timing_profile")),
        "min_net_rr": getattr(scalp_cfg, "min_net_rr", float(cfg_map["min_net_rr"]) if "min_net_rr" in cfg_map else None),
        "quality_scoring_enabled": getattr(scalp_cfg, "quality_scoring_enabled", cfg_map.get("quality_scoring_enabled") == "true" if "quality_scoring_enabled" in cfg_map else None),
        "quality_arm_threshold": getattr(scalp_cfg, "quality_arm_threshold", float(cfg_map["quality_arm_threshold"]) if "quality_arm_threshold" in cfg_map else None),
        "lesson_score_adjustment_enabled": getattr(scalp_cfg, "lesson_score_adjustment_enabled", cfg_map.get("lesson_score_adjustment_enabled") == "true" if "lesson_score_adjustment_enabled" in cfg_map else None),
    }

    return effective_config


def read_auto_authority(db: Session) -> Dict[str, Any]:
    """
    Rules 014, 030, 046: Reads auto trading authority as a true 3-state value:
    True (ON), False (OFF), or None (UNVERIFIED / NOT_CONFIGURED).
    Never creates rows, commits, or coerces None to False.
    """
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "auto_paper_trading").first()
    if cfg is None:
        return {
            "value": None,
            "source": "PERSISTED_DB",
            "reason": "NOT_CONFIGURED",
        }

    raw_val = str(cfg.value).strip().lower() if cfg.value is not None else ""
    if raw_val == "true":
        return {"value": True, "source": "PERSISTED_DB", "reason": None}
    elif raw_val == "false":
        return {"value": False, "source": "PERSISTED_DB", "reason": None}
    else:
        return {
            "value": None,
            "source": "PERSISTED_DB",
            "reason": f"INVALID_VALUE_{raw_val}",
        }


def find_existing_observation_run(
    db: Session,
    *,
    run_id: Optional[str] = None,
    symbol: str = DEFAULT_SYMBOL,
    scope: str = DEFAULT_SCOPE,
) -> Tuple[Optional[models.PaperResearchRunRecord], str]:
    """
    Rules 021-026, 047-050: Locate existing observation run without creating new runs or falling back erroneously.
    Returns (run_record, resolution_status).
    resolution_status in {"FOUND", "NOT_FOUND", "RUN_SCOPE_MISMATCH", "RUN_SCOPE_UNVERIFIED", "UNAVAILABLE"}.
    """
    if run_id:
        run = db.query(models.PaperResearchRunRecord).filter(
            models.PaperResearchRunRecord.run_id == run_id
        ).first()
        if not run:
            return None, "NOT_FOUND"
        if not run.scope:
            return run, "RUN_SCOPE_UNVERIFIED"
        if run.scope != scope or (run.symbol and run.symbol != symbol):
            return run, "RUN_SCOPE_MISMATCH"
        return run, "FOUND"

    # No specific run_id requested: find active run (COLLECTING or CREATED) matching scope & symbol
    run = (
        db.query(models.PaperResearchRunRecord)
        .filter(
            models.PaperResearchRunRecord.scope == scope,
            models.PaperResearchRunRecord.symbol == symbol,
            models.PaperResearchRunRecord.status.in_(["COLLECTING", "CREATED"]),
        )
        .order_by(models.PaperResearchRunRecord.started_at_ms.desc())
        .first()
    )
    if run:
        return run, "FOUND"

    # Do not silently fall back to completed runs without explicit run_id (Rule 025)
    return None, "NOT_FOUND"


def read_linked_orders(
    db: Session,
    *,
    run_id: str,
    symbol: str = DEFAULT_SYMBOL,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Rules 051-057: Reads orders explicitly associated with run_id via PaperResearchOrderLink.
    Deduplicates orders by ID, prevents cross-run bleeding, and identifies dangling links.
    """
    links = db.query(models.PaperResearchOrderLink).filter(
        models.PaperResearchOrderLink.run_id == run_id
    ).all()

    order_ids = [link.order_id for link in links if link.order_id]
    unique_order_ids = sorted(list(set(order_ids)))

    orders_in_db = db.query(models.PaperOrder).filter(
        models.PaperOrder.id.in_(unique_order_ids)
    ).all() if unique_order_ids else []

    order_map = {o.id: o for o in orders_in_db}

    dangling_count = 0
    mismatched_instrument_count = 0
    clean_orders: List[Dict[str, Any]] = []

    for oid in unique_order_ids:
        order = order_map.get(oid)
        if order is None:
            dangling_count += 1
            continue
        if order.instrument != symbol:
            mismatched_instrument_count += 1
            continue

        clean_orders.append({
            "id": order.id,
            "instrument": order.instrument,
            "direction": order.direction,
            "state": str(order.state).lower() if order.state else "unknown",
            "created_at": order.created_at,
            "armed_at": order.armed_at,
            "opened_at": order.opened_at,
            "closed_at": order.closed_at,
            "realized_pnl_net": float(order.realized_pnl_net) if order.realized_pnl_net is not None else None,
            "initial_risk_usdt": float(order.initial_risk_usdt) if order.initial_risk_usdt is not None else None,
            "gross_rr": float(order.gross_rr) if order.gross_rr is not None else None,
            "estimated_net_rr": float(order.estimated_net_rr) if order.estimated_net_rr is not None else None,
            "exit_cause": order.exit_cause,
        })

    metadata = {
        "total_links_found": len(links),
        "unique_orders_linked": len(unique_order_ids),
        "dangling_order_links_count": dangling_count,
        "mismatched_instrument_count": mismatched_instrument_count,
    }
    return clean_orders, metadata


def compute_window_opening_equity(
    orders: Sequence[Dict[str, Any]],
    initial_capital: Optional[float],
    start_ms: Optional[int],
) -> Dict[str, Any]:
    """
    Rules 029, 030, 032: Computes verified opening equity for a sub-window starting at start_ms.
    Accumulates verified closed outcomes strictly prior to start_ms (closed_at < start_ms).
    If any closed outcome before start_ms lacks verified PnL, completeness is UNVERIFIED
    and opening equity is None (sub-window drawdown % must be null).
    """
    cap = float(initial_capital) if (initial_capital is not None and initial_capital > 0) else 1000.0
    cap_valid = (initial_capital is not None and initial_capital > 0)

    if start_ms is None:
        return {
            "opening_equity": cap if cap_valid else None,
            "completeness": "COMPLETE" if cap_valid else "INVALID_CAPITAL",
            "accumulated_pnl_usd": 0.0,
            "prior_trades_count": 0,
        }

    accumulated_pnl = 0.0
    prior_trades_count = 0
    unverified_prior = False

    for o in orders:
        st = str(o.get("state", "")).lower()
        if st in ("closed", "paper_closed"):
            cl_at = o.get("closed_at")
            if cl_at is not None and cl_at < start_ms:
                pnl = o.get("realized_pnl_net")
                if pnl is not None and not math.isnan(pnl) and not math.isinf(pnl):
                    accumulated_pnl += float(pnl)
                    prior_trades_count += 1
                else:
                    unverified_prior = True

    if unverified_prior or not cap_valid:
        return {
            "opening_equity": None,
            "completeness": "UNVERIFIED" if unverified_prior else "INVALID_CAPITAL",
            "accumulated_pnl_usd": round(accumulated_pnl, 2),
            "prior_trades_count": prior_trades_count,
        }

    opening_equity = round(cap + accumulated_pnl, 2)
    return {
        "opening_equity": opening_equity,
        "completeness": "COMPLETE",
        "accumulated_pnl_usd": round(accumulated_pnl, 2),
        "prior_trades_count": prior_trades_count,
    }


def compute_position_state_view(
    orders: Sequence[Dict[str, Any]],
    requested_as_of_ms: Optional[int] = None,
    observed_at_ms: Optional[int] = None,
    view_mode: str = "CURRENT_PERSISTED",
) -> Dict[str, Any]:
    """
    Rules 101-104, 107, V16.39.12: Computes position state view distinguishing CURRENT_PERSISTED
    from HISTORICAL_AS_OF.
    Pure function without side-effects or synthetic event backfilling.
    """
    if view_mode == "CURRENT_PERSISTED":
        open_positions = 0
        armed_orders = 0
        for o in orders:
            c_at = o.get("created_at")
            if requested_as_of_ms is not None and c_at is not None and c_at >= requested_as_of_ms:
                continue
            st = str(o.get("state", "")).lower()
            if st in ("open", "paper_open"):
                open_positions += 1
            elif st in ("armed", "paper_armed"):
                armed_orders += 1

        return {
            "state_view": "CURRENT_PERSISTED",
            "open_positions_count": open_positions,
            "armed_orders_count": armed_orders,
            "historical_state_available": True,
            "state_as_of_ms": observed_at_ms or requested_as_of_ms,
            "state_observed_at_ms": observed_at_ms or requested_as_of_ms,
            "reason": "CURRENT_DB_STATE",
        }
    else:
        # HISTORICAL_AS_OF: Without transition logs, reconstructing past active states
        # cannot be guaranteed without ambiguity (cancellations, expirations, recovery).
        return {
            "state_view": "HISTORICAL_AS_OF",
            "open_positions_count": None,
            "armed_orders_count": None,
            "historical_state_available": False,
            "state_as_of_ms": requested_as_of_ms,
            "state_observed_at_ms": observed_at_ms,
            "reason": "HISTORICAL_TRANSITIONS_UNAVAILABLE",
        }


def compute_financial_metrics(
    orders: Sequence[Dict[str, Any]],
    *,
    initial_capital: Optional[float] = 1000.0,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    state_as_of_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
    view_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rules 029-041, 071-090, 101-115, V16.39.12: Computes pure post-fee financial metrics for the window [start_ms, end_ms).
    Start is inclusive, end is exclusive.
    Returns verified outcomes, expectancy, winrate, peak closed equity drawdown,
    and rigorous position state view.
    """
    cap = float(initial_capital) if (initial_capital is not None and initial_capital > 0) else 1000.0
    cap_valid = (initial_capital is not None and initial_capital > 0)

    # 1. Orders created in window
    orders_created_in_window = []
    state_counts: Dict[str, int] = {}

    for o in orders:
        c_at = o.get("created_at")
        in_win = True
        if c_at is not None:
            if start_ms is not None and c_at < start_ms:
                in_win = False
            if end_ms is not None and c_at >= end_ms:
                in_win = False
        else:
            in_win = False

        if in_win:
            orders_created_in_window.append(o)
            st = o.get("state", "unknown")
            state_counts[st] = state_counts.get(st, 0) + 1

    # 2. Closed outcomes falling within window [start_ms, end_ms)
    verified_closed: List[Dict[str, Any]] = []
    unresolved_closed_count = 0
    unresolved_timestamp_count = 0

    for o in orders:
        st = str(o.get("state", "")).lower()
        if st in ("closed", "paper_closed"):
            cl_at = o.get("closed_at")
            if cl_at is None:
                # Rule 036: Missing closed_at is unresolved timestamp, never silently fall back to created_at
                unresolved_timestamp_count += 1
                unresolved_closed_count += 1
                continue

            if start_ms is not None and cl_at < start_ms:
                continue
            if end_ms is not None and cl_at >= end_ms:
                continue

            pnl = o.get("realized_pnl_net")
            if pnl is not None and not math.isnan(pnl) and not math.isinf(pnl):
                verified_closed.append(o)
            else:
                unresolved_closed_count += 1

    # Sort verified closed by closed_at, tie-break by order id
    verified_closed.sort(key=lambda x: (x.get("closed_at") or 0, x.get("id") or ""))

    closed_count = len(verified_closed)
    wins = 0
    losses = 0
    breakevens = 0
    pnl_stream: List[float] = []

    for o in verified_closed:
        pnl = float(o["realized_pnl_net"])
        pnl_stream.append(pnl)
        if pnl > 0.0:
            wins += 1
        elif pnl < 0.0:
            losses += 1
        else:
            breakevens += 1

    verified_net_pnl_subtotal = round(sum(pnl_stream), 2) if pnl_stream else 0.0

    # Rules 037, 038, 039: Accounting status & Winrate
    verified_winrate_pct: Optional[float] = None
    winrate_pct: Optional[float] = None
    expectancy_usd: Optional[float] = None
    net_realized_pnl: Optional[float] = None

    if closed_count > 0:
        verified_winrate_pct = round((wins / closed_count) * 100.0, 2)

    if unresolved_closed_count > 0:
        accounting_status = "PARTIAL"
        net_realized_pnl = None  # Total PnL cannot be claimed verified when outcomes are missing
        winrate_pct = None      # Do not show 100% winrate as total when outcomes are unresolved
        expectancy_usd = None
    else:
        accounting_status = "COMPLETE"
        net_realized_pnl = verified_net_pnl_subtotal
        winrate_pct = verified_winrate_pct
        if closed_count > 0:
            expectancy_usd = round(net_realized_pnl / closed_count, 2)

    # Calculate opening equity for sub-window (Rule 029-032)
    opening_eq_info = compute_window_opening_equity(orders, initial_capital=initial_capital, start_ms=start_ms)
    window_opening_eq = opening_eq_info.get("opening_equity")

    max_dd_usd = 0.0
    max_dd_pct: Optional[float] = 0.0

    if closed_count > 0:
        if window_opening_eq is not None and window_opening_eq > 0:
            dd_usd, dd_pct = calculate_running_peak_drawdown(pnl_stream, initial_capital=window_opening_eq)
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct
        else:
            dd_usd, _ = calculate_running_peak_drawdown(pnl_stream, initial_capital=cap)
            max_dd_usd = dd_usd
            max_dd_pct = None  # Unverified opening equity means window dd % is null
    else:
        max_dd_usd = 0.0
        max_dd_pct = 0.0 if (window_opening_eq is not None or (start_ms is None and cap_valid)) else None

    # Derive position state view mode (Rules 101-106)
    effective_view_mode = view_mode
    if effective_view_mode is None:
        if state_as_of_ms is not None:
            if now_ms is not None and state_as_of_ms < now_ms:
                effective_view_mode = "HISTORICAL_AS_OF"
            elif now_ms is None:
                effective_view_mode = "HISTORICAL_AS_OF"
            else:
                effective_view_mode = "CURRENT_PERSISTED"
        elif end_ms is not None and start_ms is not None:
            if now_ms is not None and end_ms < now_ms:
                effective_view_mode = "HISTORICAL_AS_OF"
            else:
                effective_view_mode = "CURRENT_PERSISTED"
        else:
            effective_view_mode = "CURRENT_PERSISTED"

    pos_state = compute_position_state_view(
        orders,
        requested_as_of_ms=state_as_of_ms or end_ms,
        observed_at_ms=now_ms or state_as_of_ms or end_ms,
        view_mode=effective_view_mode,
    )

    return {
        "orders_count": len(orders_created_in_window),
        "state_counts": state_counts,
        "closed_trades_count": closed_count,
        "verified_closed_count": closed_count,
        "unresolved_closed_count": unresolved_closed_count,
        "unresolved_timestamp_count": unresolved_timestamp_count,
        "accounting_status": accounting_status,
        "wins_count": wins,
        "losses_count": losses,
        "breakevens_count": breakevens,
        "open_positions_count": pos_state["open_positions_count"],
        "armed_orders_count": pos_state["armed_orders_count"],
        "historical_state_available": pos_state["historical_state_available"],
        "state_view": pos_state["state_view"],
        "state_as_of_ms": pos_state["state_as_of_ms"],
        "state_observed_at_ms": pos_state["state_observed_at_ms"],
        "state_reason": pos_state["reason"],
        "net_realized_pnl_usd": net_realized_pnl,
        "verified_net_pnl_subtotal_usd": verified_net_pnl_subtotal,
        "winrate_pct": winrate_pct,
        "verified_winrate_pct": verified_winrate_pct,
        "expectancy_usd_per_trade": expectancy_usd,
        "max_drawdown_usd": max_dd_usd,
        "max_drawdown_pct": max_dd_pct,
        "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",
        "drawdown_scope": "WINDOW",
        "initial_capital_usdt": cap if cap_valid else None,
        "window_opening_equity_usdt": window_opening_eq,
    }


def read_run_funnel_records(
    db: Session,
    *,
    run_id: Optional[str] = None,
    symbol: str = DEFAULT_SYMBOL,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> List[models.OpportunityFunnelRecord]:
    """
    Rules 023-024, 062-066: Reads OpportunityFunnelRecord strictly isolated by explicit run_id.
    Never spills across runs or unlinked opportunities when run_id is missing or None.
    """
    if not run_id:
        return []

    query = db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.symbol == symbol,
        (models.OpportunityFunnelRecord.run_id == run_id)
        | (
            (models.OpportunityFunnelRecord.run_id.is_(None))
            & (models.OpportunityFunnelRecord.session_id == run_id)
        ),
    )
    if start_ms is not None:
        query = query.filter(models.OpportunityFunnelRecord.as_of_ms >= start_ms)
    if end_ms is not None:
        query = query.filter(models.OpportunityFunnelRecord.as_of_ms < end_ms)

    return query.all()


def aggregate_funnel_readonly(records: Sequence[Any]) -> Dict[str, Any]:
    """
    Rules 027, 066-069: Pure, non-mutating aggregation of OpportunityFunnelRecords.
    Differentiates unique opportunities from stage event occurrences.
    Supports canonical OpportunityStage.PLAN_VALID enum value while outputting plans_valid.
    """
    unique_opps: Set[str] = set()
    stage_counts: Dict[str, int] = {
        "ZONE_DETECTED": 0,
        "ZONE_WATCHED": 0,
        "ZONE_TOUCHED": 0,
        "TRIGGER_CONFIRMED": 0,
        "PLAN_VALID": 0,
        "PLANS_VALID": 0,
        "GUARD_BLOCKED": 0,
        "ARMED": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "INVALIDATED": 0,
    }
    reasons_counter: Dict[str, int] = {}
    latencies: List[float] = []

    for rec in records:
        opp_id = getattr(rec, "opportunity_id", None)
        if opp_id:
            unique_opps.add(str(opp_id))

        stg = str(getattr(rec, "stage", "")).upper()
        if stg in stage_counts:
            stage_counts[stg] += 1

        # Reason codes parsing
        reasons_raw = getattr(rec, "reason_codes_json", None)
        reasons_list: List[str] = []
        if isinstance(reasons_raw, str) and reasons_raw:
            try:
                parsed = json.loads(reasons_raw)
                if isinstance(parsed, list):
                    reasons_list = [str(x) for x in parsed]
            except Exception:
                pass
        elif isinstance(reasons_raw, list):
            reasons_list = [str(x) for x in reasons_raw]

        for r in reasons_list:
            reasons_counter[r] = reasons_counter.get(r, 0) + 1

        # Latency metrics parsing
        metrics_raw = getattr(rec, "metrics_json", None)
        if isinstance(metrics_raw, str) and metrics_raw:
            try:
                m_obj = json.loads(metrics_raw)
                t_start = m_obj.get("evaluation_started_at")
                t_done = m_obj.get("evaluation_completed_at")
                if (
                    t_start is not None
                    and t_done is not None
                    and t_done >= t_start
                    and not math.isnan(t_start)
                    and not math.isnan(t_done)
                ):
                    latencies.append(float(t_done - t_start))
            except Exception:
                pass

    # Enrich top rejection reasons with classify_reason
    top_reasons_enriched = []
    sorted_reasons = sorted(reasons_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    for code, count in sorted_reasons[:5]:
        cat, msg_vi = classify_reason(code)
        if not msg_vi or msg_vi == code:
            msg_vi = f"Mã kiểm tra: {code} (Chưa có mô tả chi tiết)"
        top_reasons_enriched.append({
            "code": code,
            "count": count,
            "category": cat or "GENERAL",
            "message_vi": msg_vi,
        })

    lat_median: Optional[float] = None
    lat_p95: Optional[float] = None
    if latencies:
        latencies.sort()
        n = len(latencies)
        lat_median = round(latencies[n // 2], 2)
        p95_idx = min(int(n * 0.95), n - 1)
        lat_p95 = round(latencies[p95_idx], 2)

    return {
        "total_unique_opportunities": len(unique_opps),
        "zones_detected": stage_counts["ZONE_DETECTED"],
        "zones_watched": stage_counts["ZONE_WATCHED"],
        "zones_touched": stage_counts["ZONE_TOUCHED"],
        "trigger_confirmed": stage_counts["TRIGGER_CONFIRMED"],
        "plans_valid": stage_counts["PLAN_VALID"] + stage_counts["PLANS_VALID"],
        "guard_blocked": stage_counts["GUARD_BLOCKED"],
        "armed": stage_counts["ARMED"],
        "filled": stage_counts["FILLED"],
        "expired": stage_counts["EXPIRED"],
        "invalidated": stage_counts["INVALIDATED"],
        "top_rejection_reasons": top_reasons_enriched,
        "latency_metrics": {
            "sample_count": len(latencies),
            "median_ms": lat_median,
            "p95_ms": lat_p95,
        },
    }


def read_process_health(runtime_providers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Rules 091-096: Reports truthful worker process health.
    - If live runtime provider is injected: reads actual scheduler health.
    - If offline (no provider): status is UNKNOWN, running is None.
    """
    if runtime_providers and "scheduler" in runtime_providers:
        sched = runtime_providers["scheduler"]
        if hasattr(sched, "get_health_snapshot"):
            snap = sched.get_health_snapshot()
            return {
                "status": snap.get("status", "UNKNOWN"),
                "running": snap.get("running"),
                "enabled": snap.get("enabled"),
                "effective_owner": snap.get("effective_owner", "UNKNOWN"),
                "role": snap.get("role", "UNKNOWN"),
                "source": "LIVE_BACKEND_PROCESS",
                "detail": "Live in-process scheduler state observed.",
            }

    return {
        "status": "UNKNOWN",
        "running": None,
        "source": "OFFLINE_DB_READ_ONLY",
        "detail": "Worker process health cannot be determined from offline database alone.",
    }


def validate_finite_metrics(payload: Any) -> None:
    """
    Rule 037: Recursively ensures all numeric floats in payload are finite (not NaN, Inf, -Inf).
    """
    if isinstance(payload, float):
        if math.isnan(payload) or math.isinf(payload):
            raise ValueError(f"Non-finite float value detected in metrics: {payload}")
    elif isinstance(payload, dict):
        for k, v in payload.items():
            validate_finite_metrics(v)
    elif isinstance(payload, list):
        for item in payload:
            validate_finite_metrics(item)


def build_paper_runtime_snapshot(
    db: Optional[Session],
    *,
    run_id: Optional[str] = None,
    symbol: str = DEFAULT_SYMBOL,
    scope: str = DEFAULT_SCOPE,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
    runtime_providers: Optional[Dict[str, Any]] = None,
    source_mode: str = "OFFLINE_DB",
) -> Dict[str, Any]:
    """
    Rules 021-040, 125-127: The single canonical snapshot builder shared by API endpoint and CLI.
    Never mutates state, executes fills, or creates synthetic data.
    """
    ctx = make_snapshot_context(
        symbol=symbol,
        scope=scope,
        requested_run_id=run_id,
        start_ms=start_ms,
        end_ms=end_ms,
        now_ms=now_ms,
    )
    commit_sha = get_git_commit()
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    limitations: List[str] = []

    # Database accessibility
    if db is None:
        worker_health = read_process_health(runtime_providers)
        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": ctx["now_ms"],
            "source_mode": source_mode,
            "status": "RUNTIME_NOT_AVAILABLE",
            "runtime_commit": commit_sha,
            "reporter_commit": commit_sha,
            "time_window": {
                "start_ms": ctx["start_ms"],
                "end_ms": ctx["end_ms"],
            },
            "effective_config": {"auto_enabled": None},
            "worker_health": worker_health,
            "run_summary": {
                "status": "UNAVAILABLE",
                "resolution_status": "UNAVAILABLE",
                "run_id": ctx["requested_run_id"] or "NONE",
            },
            "funnel_summary": aggregate_funnel_readonly([]),
            "metrics": {
                "duration_minutes": 0.0,
                "opportunities_count": 0,
                "orders_count": 0,
                "open_positions_count": 0,
                "armed_orders_count": 0,
                "closed_trades_count": 0,
                "net_realized_pnl_usd": None,
                "winrate_pct": None,
                "expectancy_usd_per_trade": None,
                "max_drawdown_usd": 0.0,
                "max_drawdown_pct": None,
                "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",
            },
            "errors": [{"code": "DATABASE_NOT_PROVIDED", "message_vi": "Chưa kết nối được với cơ sở dữ liệu."}],
            "warnings": [],
            "limitations": ["RUNTIME_NOT_AVAILABLE: Cơ sở dữ liệu không khả dụng."],
        }
        return snapshot

    # 1. Effective Config & 3-state Auto
    effective_config: Dict[str, Any] = {}
    auto_authority: Dict[str, Any] = {"value": None, "source": "UNKNOWN", "reason": "NOT_READ"}

    try:
        effective_config = read_existing_policy_config(db)
        auto_authority = read_auto_authority(db)
        effective_config["auto_enabled"] = auto_authority["value"]
        effective_config["auto_source"] = auto_authority["source"]
        effective_config["auto_reason"] = auto_authority["reason"]
    except Exception as e:
        logger.warning(f"Failed to read policy or auto config: {e}")
        warnings.append({
            "code": "CONFIG_READ_PARTIAL",
            "message_vi": "Không đọc được đầy đủ cấu hình TradingPolicy hoặc SystemConfig.",
            "component": "CONFIG",
        })
        effective_config["auto_enabled"] = None

    if auto_authority["value"] is None:
        limitations.append("AUTO_UNVERIFIED: Quyền tự động (Auto Trading) chưa được xác minh hoặc chưa cấu hình.")
    elif auto_authority["value"] is False:
        limitations.append("AUTO_OFF: Quyền tự động (Auto Trading) đang TẮT. Hệ thống không tự động vào lệnh.")

    # 2. Worker health
    worker_health = read_process_health(runtime_providers)
    if worker_health["status"] == "UNKNOWN":
        limitations.append("OFFLINE_MODE: Không thể xác nhận trạng thái worker từ ảnh chụp cơ sở dữ liệu.")
    elif worker_health["status"] == "STOPPED":
        limitations.append("WORKER_STOPPED: Worker đánh giá chiến lược hiện đang DỪNG.")

    # 3. Locate Run
    run_record, resolution_status = find_existing_observation_run(
        db,
        run_id=ctx["requested_run_id"],
        symbol=ctx["symbol"],
        scope=ctx["scope"],
    )

    run_summary: Dict[str, Any] = {
        "run_id": ctx["requested_run_id"] or (run_record.run_id if run_record else "NONE"),
        "resolution_status": resolution_status,
        "status": run_record.status if run_record else "NOT_FOUND",
        "symbol": ctx["symbol"],
        "scope": ctx["scope"],
    }

    linked_orders: List[Dict[str, Any]] = []
    initial_cap: Optional[float] = 1000.0

    if run_record and resolution_status == "FOUND":
        run_summary.update({
            "started_at_ms": run_record.started_at_ms,
            "ended_at_ms": run_record.ended_at_ms,
            "execution_mode": run_record.execution_mode,
            "observation_mode": run_record.observation_mode,
            "initial_capital_usdt": run_record.initial_capital_usdt,
            "config_fingerprint": run_record.config_fingerprint,
            "software_version": run_record.software_version,
        })
        if run_record.initial_capital_usdt:
            initial_cap = float(run_record.initial_capital_usdt)

        eff_start = ctx["start_ms"] if ctx["start_ms"] is not None else run_record.started_at_ms
        eff_end = ctx["end_ms"]
        if run_record.ended_at_ms and ctx["end_ms"] > run_record.ended_at_ms:
            eff_end = run_record.ended_at_ms

        linked_orders, assoc_meta = read_linked_orders(db, run_id=run_record.run_id, symbol=ctx["symbol"])
        run_summary["association_metadata"] = assoc_meta

        # Funnel strictly for resolved run
        funnel_records = read_run_funnel_records(
            db,
            run_id=run_record.run_id,
            symbol=ctx["symbol"],
            start_ms=eff_start,
            end_ms=eff_end,
        )
        funnel_summary = aggregate_funnel_readonly(funnel_records)

        now_ms = ctx["now_ms"]
        calc_view_mode = "HISTORICAL_AS_OF" if (eff_end is not None and eff_end < now_ms) else "CURRENT_PERSISTED"

        # Financial Metrics
        fin_metrics = compute_financial_metrics(
            linked_orders,
            initial_capital=initial_cap,
            start_ms=eff_start,
            end_ms=eff_end,
            state_as_of_ms=eff_end,
            now_ms=now_ms,
            view_mode=calc_view_mode,
        )
    else:
        # Rules 021-024: Run not resolved or mismatch. NEVER calculate financial metrics or leak funnel!
        eff_start = ctx["start_ms"]
        eff_end = ctx["end_ms"]
        funnel_summary = aggregate_funnel_readonly([])
        fin_metrics = {
            "orders_count": 0,
            "state_counts": {},
            "closed_trades_count": 0,
            "verified_closed_count": 0,
            "unresolved_closed_count": 0,
            "accounting_status": "UNAVAILABLE",
            "net_realized_pnl_usd": None,
            "verified_net_pnl_subtotal_usd": 0.0,
            "winrate_pct": None,
            "verified_winrate_pct": None,
            "expectancy_usd_per_trade": None,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": None,
            "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",
            "drawdown_scope": "WINDOW",
            "open_positions_count": None,
            "armed_orders_count": None,
            "historical_state_available": False,
            "state_view": "UNAVAILABLE",
            "state_as_of_ms": eff_end,
            "state_observed_at_ms": ctx["now_ms"],
            "state_reason": "RUN_NOT_RESOLVED",
        }

        if resolution_status == "NOT_FOUND":
            limitations.append(f"RUN_NOT_FOUND: Không tìm thấy phiên quan sát phù hợp (run_id={ctx['requested_run_id']}).")
        elif resolution_status == "RUN_SCOPE_MISMATCH":
            limitations.append(f"RUN_SCOPE_MISMATCH: Phiên {ctx['requested_run_id']} không thuộc symbol {ctx['symbol']} / scope {ctx['scope']}.")
            if run_record:
                run_summary.update({
                    "started_at_ms": run_record.started_at_ms,
                    "ended_at_ms": run_record.ended_at_ms,
                    "status": run_record.status,
                    "actual_scope": run_record.scope,
                    "actual_symbol": run_record.symbol,
                })
        elif resolution_status == "RUN_SCOPE_UNVERIFIED":
            limitations.append(f"RUN_SCOPE_UNVERIFIED: Phiên {ctx['requested_run_id']} không có scope được ghi nhận.")

    # Calculate observed duration
    duration_min = 0.0
    if eff_start is not None and eff_end is not None and eff_end >= eff_start:
        duration_min = round((eff_end - eff_start) / (1000.0 * 60.0), 1)

    if fin_metrics["closed_trades_count"] == 0:
        limitations.append("INSUFFICIENT_SAMPLE: 0 lệnh đóng đã xác nhận. Tỷ lệ thắng và kỳ vọng là N/A.")

    # 6. Overall Status Determination (Rules 028, 031)
    if resolution_status in ("NOT_FOUND", "RUN_SCOPE_MISMATCH", "RUN_SCOPE_UNVERIFIED"):
        overall_status = "NOT_CONFIGURED"
    elif worker_health["status"] == "STOPPED":
        overall_status = "STOPPED"
    elif worker_health["status"] == "RUNNING":
        overall_status = "RUNNING"
    else:
        overall_status = "SNAPSHOT_READY"

    metrics_payload = {
        "duration_minutes": duration_min,
        "opportunities_count": funnel_summary["total_unique_opportunities"],
        "orders_count": fin_metrics["orders_count"],
        "state_counts": fin_metrics["state_counts"],
        "open_positions_count": fin_metrics["open_positions_count"],
        "armed_orders_count": fin_metrics["armed_orders_count"],
        "historical_state_available": fin_metrics.get("historical_state_available", False),
        "state_view": fin_metrics.get("state_view", "CURRENT_PERSISTED"),
        "state_as_of_ms": fin_metrics.get("state_as_of_ms"),
        "state_observed_at_ms": fin_metrics.get("state_observed_at_ms"),
        "state_reason": fin_metrics.get("state_reason"),
        "closed_trades_count": fin_metrics["closed_trades_count"],
        "verified_closed_count": fin_metrics.get("verified_closed_count", fin_metrics["closed_trades_count"]),
        "unresolved_closed_count": fin_metrics["unresolved_closed_count"],
        "accounting_status": fin_metrics.get("accounting_status", "COMPLETE"),
        "net_realized_pnl_usd": fin_metrics["net_realized_pnl_usd"],
        "verified_net_pnl_subtotal_usd": fin_metrics.get("verified_net_pnl_subtotal_usd", 0.0),
        "winrate_pct": fin_metrics["winrate_pct"],
        "verified_winrate_pct": fin_metrics.get("verified_winrate_pct"),
        "expectancy_usd_per_trade": fin_metrics["expectancy_usd_per_trade"],
        "max_drawdown_usd": fin_metrics["max_drawdown_usd"],
        "max_drawdown_pct": fin_metrics["max_drawdown_pct"],
        "drawdown_type": fin_metrics["drawdown_type"],
        "drawdown_scope": fin_metrics.get("drawdown_scope", "WINDOW"),
    }

    validate_finite_metrics(metrics_payload)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ctx["now_ms"],
        "source_mode": source_mode,
        "status": overall_status,
        "runtime_commit": commit_sha,
        "reporter_commit": commit_sha,
        "time_window": {
            "start_ms": eff_start,
            "end_ms": eff_end,
        },
        "effective_config": effective_config,
        "worker_health": worker_health,
        "run_summary": run_summary,
        "funnel_summary": funnel_summary,
        "metrics": metrics_payload,
        "errors": errors,
        "warnings": warnings,
        "limitations": limitations,
    }


def render_markdown(snapshot: Dict[str, Any]) -> str:
    """
    Rules 136-145: Renders human-friendly, transparent Markdown report.
    - Explicit 3-line beginner header.
    - 3-state Auto authority (BẬT / TẮT / CHƯA XÁC MINH).
    - Offline worker status shows unconfirmed without guessing.
    - Expectancy renders 0.00 USD/lệnh on breakeven, N/A on 0 trades.
    """
    metrics = snapshot.get("metrics", {})
    worker = snapshot.get("worker_health", {})
    cfg = snapshot.get("effective_config", {})
    funnel = snapshot.get("funnel_summary", {})
    limitations = snapshot.get("limitations", [])
    run_sum = snapshot.get("run_summary", {})

    status = snapshot.get("status", "UNKNOWN")
    dur_min = metrics.get("duration_minutes", 0)
    opps = metrics.get("opportunities_count", 0)
    orders = metrics.get("orders_count", 0)

    pnl = metrics.get("net_realized_pnl_usd")
    unresolved_c = metrics.get("unresolved_closed_count", 0)
    verified_subtotal = metrics.get("verified_net_pnl_subtotal_usd", 0.0)
    v_closed_c = metrics.get("verified_closed_count", metrics.get("closed_trades_count", 0))

    if pnl is not None:
        pnl_str = f"{pnl:+.2f} USD"
    elif unresolved_c > 0 and v_closed_c > 0:
        pnl_str = f"Chưa xác minh toàn bộ (Phần đã xác nhận: {verified_subtotal:+.2f} USD trên {v_closed_c} lệnh; {unresolved_c} lệnh chưa rõ)"
    else:
        pnl_str = "Chưa có dữ liệu xác minh"

    winrate = metrics.get("winrate_pct")
    verified_wr = metrics.get("verified_winrate_pct")
    if winrate is not None:
        winrate_str = f"{winrate}%"
    elif verified_wr is not None and unresolved_c > 0:
        winrate_str = f"{verified_wr}% (trên {v_closed_c} kết quả đã xác nhận; {unresolved_c} chưa rõ)"
    else:
        winrate_str = "N/A (chưa đủ mẫu lệnh đóng)"

    exp = metrics.get("expectancy_usd_per_trade")
    exp_str = f"{exp:+.2f} USD/lệnh" if exp is not None else "N/A (chưa đủ mẫu lệnh đóng)"

    # Rule 137: Auto authority rendering
    auto_val = cfg.get("auto_enabled")
    if auto_val is True:
        auto_str = "BẬT (ON)"
    elif auto_val is False:
        auto_str = "TẮT (OFF)"
    else:
        auto_str = "CHƯA XÁC MINH (UNVERIFIED)"

    # Rule 138: Worker health rendering
    worker_running = worker.get("running")
    if worker_running is None:
        worker_str = f"{worker.get('status', 'UNKNOWN')} (Không xác nhận được từ ảnh chụp DB)"
    elif worker_running is True:
        worker_str = f"RUNNING ({worker.get('role', 'ACTIVE')})"
    else:
        worker_str = f"STOPPED"

    # Beginner 3-line header (Rule 136)
    beginner_summary = (
        f"**Đã quan sát:** {dur_min} phút · {opps} cơ hội · {orders} lệnh.\n"
        f"**Lời/lỗ sau phí:** {pnl_str}.\n"
        f"**Tỷ lệ thắng:** {winrate_str}."
    )

    top_reasons = funnel.get("top_rejection_reasons", [])
    reasons_table = []
    if top_reasons:
        reasons_table.append("| Mã chặn | Số lần | Nhóm | Giải thích |")
        reasons_table.append("| :--- | :--- | :--- | :--- |")
        for r in top_reasons:
            reasons_table.append(
                f"| `{r['code']}` | {r['count']} | {r.get('category', 'GENERAL')} | {r['message_vi']} |"
            )
    else:
        reasons_table.append("*(Chưa ghi nhận sự kiện chặn trong khoảng thời gian này)*")

    limitations_list = "\n".join(f"- {item}" for item in limitations) if limitations else "- Không có cảnh báo hạn chế."

    commit_short = str(snapshot.get("runtime_commit", "UNKNOWN"))[:12]

    dd_usd = metrics.get('max_drawdown_usd', 0.0)
    dd_pct = metrics.get('max_drawdown_pct')
    dd_pct_str = f"{dd_pct:.2f}%" if dd_pct is not None else "Chưa xác minh"

    # Null-safe historical open positions display (Rule 113)
    open_pos = metrics.get('open_positions_count')
    open_pos_str = str(open_pos) if open_pos is not None else "Chưa đủ lịch sử xác minh"

    md = f"""# Báo Cáo Quan Sát Vận Hành PAPER (V16.39.11)

> {beginner_summary}

---

## 1. Trạng Thái Vận Hành
- **Tình trạng tổng thể**: `{status}`
- **Nguồn dữ liệu**: `{snapshot.get('source_mode')}`
- **Commit mã nguồn**: `{commit_short}`
- **Worker thực thi**: `{worker_str}`
- **Quyền tự động (Auto Trading)**: `{auto_str}`
- **Mã phiên quan sát (Run ID)**: `{run_sum.get('run_id')}` (Trạng thái: `{run_sum.get('status')}`)

## 2. Phễu Cơ Hội & Thực Thi (Opportunity Funnel)
- Vùng quét (Zones Detected): **{funnel.get('zones_detected', 0)}**
- Chờ kích hoạt (Trigger Confirmed): **{funnel.get('trigger_confirmed', 0)}**
- Kế hoạch hợp lệ (Plans Valid): **{funnel.get('plans_valid', 0)}**
- Lệnh đã đặt chờ (ARMED): **{funnel.get('armed', 0)}**
- Lệnh đã khớp (FILLED): **{funnel.get('filled', 0)}**
- Vị thế đang mở (Open Positions): **{open_pos_str}**

### Lý do chặn/bỏ lỡ chính:
{chr(10).join(reasons_table)}

## 3. Kết Quả Tài Chính Sau Phí (Post-Fee Performance)
- **Tổng lệnh đã đóng (Closed Trades)**: `{metrics.get('closed_trades_count', 0)}` (Chưa xác nhận: `{metrics.get('unresolved_closed_count', 0)}`)
- **Lợi nhuận ròng (Net Realized PnL)**: `{pnl_str}`
- **Tỷ lệ thắng (Winrate)**: `{winrate_str}`
- **Kỳ vọng bình quân (Expectancy)**: `{exp_str}`
- **Sụt giảm vốn đỉnh (Max Drawdown)**: `{dd_usd:.2f} USD ({dd_pct_str})`

## 4. Hạn Chế & Lưu Ý Nghiệm Thu (Limitations)
{limitations_list}

## 5. Bước Tiếp Theo
1. Nếu quyền Auto đang `CHƯA XÁC MINH` hoặc `TẮT`, giữ nguyên trạng thái quan sát cho đến khi người dùng quyết định bật.
2. Không cưỡng ép lệnh (No forced trades) chỉ để đạt chỉ tiêu ngày.
3. Khi phiên Mỹ (NY) mở cửa, kiểm tra lại snapshot để đánh giá phản ứng giá và các vùng FVG/Sweep thực tế.
"""
    return md
