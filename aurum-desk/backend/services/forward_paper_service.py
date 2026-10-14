"""
backend/services/forward_paper_service.py
V14.4.12 — Forward Paper Practice Session & Truthful Reporting Service.

Enforces:
1. Frozen session configuration (capital, policy, risk config, commit SHA).
2. Clean persistence via SystemConfig without mutating user live account or resetting equity.
3. Separation of STANDARD_SMC and NY_QUOTA_PAPER family performance.
4. Truthful reporting metrics:
   - Confirmed closed trades denominator only (pending/review orders excluded).
   - Realized drawdown from closed trade equity (no fabricated curves).
   - Safe division by zero (no NaN / Infinity).
   - INSUFFICIENT_SAMPLE flag if < 30 confirmed trades.
5. Exportable trade data (matching DB values, user notes preserved).
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import load_due_review_tasks

logger = logging.getLogger("forward_paper")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class ForwardSessionConfig:
    session_id: str
    strategy_commit: str
    start_time_ms: int
    virtual_capital: float = 1000.0
    frozen_risk_config: Dict[str, Any] = field(default_factory=dict)
    session_policy: str = "ALL_SESSIONS_WITH_NY_RESERVE"
    enabled_strategy_families: List[str] = field(
        default_factory=lambda: ["STANDARD_SMC", "NY_QUOTA_PAPER"]
    )
    cost_assumptions: Dict[str, Any] = field(default_factory=dict)
    data_source: str = "REALTIME_OR_STREAM"
    status: str = "ACTIVE"
    updated_at_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "strategy_commit": self.strategy_commit,
            "start_time_ms": self.start_time_ms,
            "virtual_capital": self.virtual_capital,
            "frozen_risk_config": dict(self.frozen_risk_config),
            "session_policy": self.session_policy,
            "enabled_strategy_families": list(self.enabled_strategy_families),
            "cost_assumptions": dict(self.cost_assumptions),
            "data_source": self.data_source,
            "status": self.status,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ForwardSessionConfig:
        return cls(
            session_id=str(data.get("session_id") or ""),
            strategy_commit=str(data.get("strategy_commit") or "HEAD"),
            start_time_ms=int(data.get("start_time_ms") or 0),
            virtual_capital=float(data.get("virtual_capital") or 1000.0),
            frozen_risk_config=dict(data.get("frozen_risk_config") or {}),
            session_policy=str(data.get("session_policy") or "ALL_SESSIONS_WITH_NY_RESERVE"),
            enabled_strategy_families=list(
                data.get("enabled_strategy_families") or ["STANDARD_SMC", "NY_QUOTA_PAPER"]
            ),
            cost_assumptions=dict(data.get("cost_assumptions") or {}),
            data_source=str(data.get("data_source") or "REALTIME_OR_STREAM"),
            status=str(data.get("status") or "ACTIVE"),
            updated_at_ms=int(data.get("updated_at_ms") or 0),
        )


def save_forward_session(db: Session, config: ForwardSessionConfig) -> bool:
    """Saves forward paper session configuration into SystemConfig."""
    key = f"fwd_sess:{config.session_id}"[:50]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    config.updated_at_ms = now_ms
    val_json = json.dumps(config.to_dict())

    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec:
        rec = models.SystemConfig(key=key, value=val_json, updated_at=now_ms)
        db.add(rec)
    else:
        rec.value = val_json
        rec.updated_at = now_ms

    # Also maintain active pointer if active
    if config.status == "ACTIVE":
        active_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_sess:active").first()
        if not active_rec:
            active_rec = models.SystemConfig(key="fwd_sess:active", value=config.session_id, updated_at=now_ms)
            db.add(active_rec)
        else:
            active_rec.value = config.session_id
            active_rec.updated_at = now_ms

    try:
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save forward paper session {config.session_id}: {e}")
        return False


def load_forward_session(db: Session, session_id: str) -> Optional[ForwardSessionConfig]:
    """Loads a forward paper session configuration from SystemConfig."""
    key = f"fwd_sess:{session_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return None
    try:
        return ForwardSessionConfig.from_dict(json.loads(rec.value))
    except Exception as e:
        logger.error(f"Failed to parse forward paper session {session_id}: {e}")
        return None


def load_active_forward_session(db: Session) -> Optional[ForwardSessionConfig]:
    """Loads current active forward paper session."""
    active_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_sess:active").first()
    if not active_rec or not active_rec.value:
        return None
    return load_forward_session(db, active_rec.value)


def create_forward_paper_session(
    db: Session,
    session_id: Optional[str] = None,
    virtual_capital: float = 1000.0,
    session_policy: str = "ALL_SESSIONS_WITH_NY_RESERVE",
    risk_config: Optional[Dict[str, Any]] = None,
    cost_assumptions: Optional[Dict[str, Any]] = None,
    commit_sha: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> ForwardSessionConfig:
    """PHẦN 043, 044, 045: Creates a new forward paper practice session with frozen parameters.
    Does NOT overwrite user live account settings or reset existing trade history.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess_id = session_id or f"fwd_{datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    frozen_risk = risk_config or {
        "risk_pct": 0.25,
        "max_daily_fills": 3,
        "min_net_rr": 2.0,
        "leverage": 5,
        "margin_mode": "ISOLATED",
    }
    frozen_costs = cost_assumptions or {
        "fees_pct": 0.10,
        "slippage_pct": 0.10,
    }

    config = ForwardSessionConfig(
        session_id=sess_id,
        strategy_commit=commit_sha or "3a34b6b6",
        start_time_ms=now_ms,
        virtual_capital=virtual_capital,
        frozen_risk_config=frozen_risk,
        session_policy=session_policy,
        enabled_strategy_families=["STANDARD_SMC", "NY_QUOTA_PAPER"],
        cost_assumptions=frozen_costs,
        data_source="REALTIME_OR_STREAM",
        status="ACTIVE",
        updated_at_ms=now_ms,
    )
    save_forward_session(db, config)
    return config


def generate_forward_paper_report(
    db: Session,
    session_id: str,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 055–058: Generates truthful statistical report for a forward paper practice session.
    Guarantees:
    - Pure read query: does NOT mutate trading state in DB.
    - Confirmed outcomes only in winrate/drawdown denominator.
    - Realized drawdown computed from closed trade equity curve.
    - Safe math: no NaN, no Infinity.
    - INSUFFICIENT_SAMPLE flag if < 30 confirmed trades.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess = load_forward_session(db, session_id)
    start_ts = sess.start_time_ms if sess else 0
    starting_capital = sess.virtual_capital if sess else 1000.0

    # Query orders associated with session or since session start
    orders_query = db.query(models.PaperOrder).filter(
        models.PaperOrder.created_at >= start_ts
    )
    if sess:
        orders_query = orders_query.filter(
            (models.PaperOrder.session_instance_id == sess.session_id) |
            (models.PaperOrder.created_at >= sess.start_time_ms)
        )
    orders = orders_query.order_by(models.PaperOrder.created_at.asc()).all()

    # Categorize orders
    total_fills = 0
    confirmed_closed: List[models.PaperOrder] = []
    pending_orders: List[models.PaperOrder] = []
    armed_orders: List[models.PaperOrder] = []
    candidate_orders: List[models.PaperOrder] = []

    # Review records query for pending/quarantine counts
    review_recs = db.query(models.SystemConfig).filter(models.SystemConfig.key.like("recon_rev:%")).all()
    active_review_count = 0
    for r in review_recs:
        try:
            d = json.loads(r.value)
            if d.get("status") in ("PENDING_REVIEW", "RETRY_SCHEDULED", "RETRYING"):
                active_review_count += 1
        except Exception:
            continue

    for o in orders:
        st = getattr(o, "state", "")
        if st in ("paper_open", "closed"):
            total_fills += 1
        if st == "closed":
            confirmed_closed.append(o)
        elif st == "paper_open":
            pending_orders.append(o)
        elif st == "armed":
            armed_orders.append(o)
        elif st in ("candidate", "draft"):
            candidate_orders.append(o)

    # Calculate days observed
    if start_ts > 0:
        dt_start = datetime.fromtimestamp(start_ts / 1000, tz=VN_TZ)
        dt_end = datetime.fromtimestamp(now_ms / 1000, tz=VN_TZ)
        days_observed = max(1, (dt_end.date() - dt_start.date()).days + 1)
    else:
        days_observed = 0

    # Confirmed outcomes metrics
    wins = [o for o in confirmed_closed if (o.realized_pnl_net or 0.0) > 0]
    losses = [o for o in confirmed_closed if (o.realized_pnl_net or 0.0) < 0]
    breakevens = [o for o in confirmed_closed if (o.realized_pnl_net or 0.0) == 0]

    confirmed_count = len(confirmed_closed)
    net_pnl = sum((o.realized_pnl_net or 0.0) for o in confirmed_closed)
    winrate = (len(wins) / confirmed_count) if confirmed_count > 0 else 0.0

    # Profit factor calculation
    total_win_pnl = sum((o.realized_pnl_net or 0.0) for o in wins)
    total_loss_pnl = sum((o.realized_pnl_net or 0.0) for o in losses)
    if abs(total_loss_pnl) > 0:
        profit_factor: Optional[float] = round(total_win_pnl / abs(total_loss_pnl), 3)
    else:
        profit_factor = None  # PHẦN 056: No division by zero

    # Expectancy in R
    valid_rs = [o.realized_r for o in confirmed_closed if o.realized_r is not None]
    expectancy_r = round(sum(valid_rs) / len(valid_rs), 3) if valid_rs else 0.0

    # Realized drawdown curve
    running_equity = starting_capital
    peak_equity = starting_capital
    max_drawdown_usd = 0.0
    for o in sorted(confirmed_closed, key=lambda x: getattr(x, "closed_at", 0) or 0):
        running_equity += (o.realized_pnl_net or 0.0)
        if running_equity > peak_equity:
            peak_equity = running_equity
        dd = peak_equity - running_equity
        if dd > max_drawdown_usd:
            max_drawdown_usd = dd

    drawdown_pct = round((max_drawdown_usd / peak_equity * 100.0), 2) if peak_equity > 0 else 0.0

    # Strategy family split (STANDARD_SMC vs NY_QUOTA_PAPER)
    standard_trades = [o for o in confirmed_closed if getattr(o, "strategy_family", "") == "STANDARD_SMC"]
    fallback_trades = [o for o in confirmed_closed if getattr(o, "strategy_family", "") == "NY_QUOTA_PAPER"]

    def _family_stats(trades: List[models.PaperOrder]) -> Dict[str, Any]:
        cnt = len(trades)
        w = [t for t in trades if (t.realized_pnl_net or 0.0) > 0]
        l = [t for t in trades if (t.realized_pnl_net or 0.0) < 0]
        pnl = sum((t.realized_pnl_net or 0.0) for t in trades)
        wr = (len(w) / cnt) if cnt > 0 else 0.0
        return {
            "total_trades": cnt,
            "wins": len(w),
            "losses": len(l),
            "net_pnl": round(pnl, 2),
            "winrate": round(wr, 3),
        }

    # Session split
    session_map = {"ASIA": [], "LONDON": [], "NEW_YORK": []}
    for o in confirmed_closed:
        sess_tag = getattr(o, "session_instance_id", "") or ""
        tag_upper = sess_tag.upper()
        if "ASIA" in tag_upper or "TOKYO" in tag_upper:
            session_map["ASIA"].append(o)
        elif "LON" in tag_upper:
            session_map["LONDON"].append(o)
        elif "NY" in tag_upper:
            session_map["NEW_YORK"].append(o)
        else:
            # Check opened_at hour in VN time
            op_at = getattr(o, "opened_at", 0) or getattr(o, "created_at", 0) or 0
            if op_at > 0:
                h = datetime.fromtimestamp(op_at / 1000, tz=VN_TZ).hour
                if 6 <= h < 14:
                    session_map["ASIA"].append(o)
                elif 14 <= h < 19:
                    session_map["LONDON"].append(o)
                else:
                    session_map["NEW_YORK"].append(o)

    session_breakdown = {
        s_name: _family_stats(s_orders)
        for s_name, s_orders in session_map.items()
    }

    # Sample adequacy
    sample_status = "SUFFICIENT_SAMPLE" if confirmed_count >= 30 else "INSUFFICIENT_SAMPLE"

    # Needs improvement notes
    needs_improvement = []
    if active_review_count > 0:
        needs_improvement.append(f"{active_review_count} order(s) require reconciliation review or quarantine inspection")
    if sample_status == "INSUFFICIENT_SAMPLE":
        needs_improvement.append(f"Sample count ({confirmed_count}) below 30-trade minimum for statistical edge validation")
    if len(losses) > len(wins) and confirmed_count > 5:
        needs_improvement.append("Loss count exceeds wins; review stop-loss placement and liquidity sweep confirmations")

    # Next steps recommendations
    next_steps = [
        "Continue forward practice without manual intervention or strategy parameter adjustments",
        "Maintain strict NY session quota and single position limit",
    ]
    if active_review_count > 0:
        next_steps.insert(0, "Investigate corrupt snapshots or data gaps for quarantined review orders")

    return {
        "session_id": session_id,
        "days_observed": days_observed,
        "total_fills": total_fills,
        "confirmed_closed_trades": confirmed_count,
        "confirmed_wins": len(wins),
        "confirmed_losses": len(losses),
        "breakevens": len(breakevens),
        "net_pnl": round(net_pnl, 2),
        "winrate": round(winrate, 3),
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "realized_drawdown_usd": round(max_drawdown_usd, 2),
        "realized_drawdown_pct": drawdown_pct,
        "pending_count": len(pending_orders),
        "review_count": active_review_count,
        "sample_status": sample_status,
        "strategy_breakdown": {
            "standard_smc": _family_stats(standard_trades),
            "ny_quota_paper": _family_stats(fallback_trades),
            "combined": _family_stats(confirmed_closed),
        },
        "session_breakdown": session_breakdown,
        "needs_improvement": needs_improvement,
        "next_steps": next_steps,
    }


def export_forward_paper_data(
    db: Session,
    session_id: str,
    format: str = "dict",
) -> Any:
    """PHẦN 071: Exports forward paper trade records with exact canonical fields.
    Does not invent fabricated values; missing values remain None / empty.
    """
    sess = load_forward_session(db, session_id)
    start_ts = sess.start_time_ms if sess else 0

    orders = (
        db.query(models.PaperOrder)
        .filter(models.PaperOrder.created_at >= start_ts)
        .order_by(models.PaperOrder.created_at.asc())
        .all()
    )

    rows = []
    for o in orders:
        # Load user review / notes if present
        review = db.query(models.TradeReview).filter(models.TradeReview.trade_id == o.id).first()
        row = {
            "order_id": o.id,
            "session_instance_id": getattr(o, "session_instance_id", "") or "",
            "direction": o.direction,
            "strategy_family": getattr(o, "strategy_family", "STANDARD_SMC"),
            "state": o.state,
            "planned_entry": o.planned_entry,
            "actual_entry": o.actual_entry,
            "stop_loss": o.stop_loss,
            "take_profit": o.take_profit,
            "actual_exit": o.actual_exit,
            "fees": getattr(o, "fees_assumption", 0.0),
            "realized_pnl_net": o.realized_pnl_net,
            "realized_r": o.realized_r,
            "exit_cause": o.exit_cause,
            "invalidation_reason": o.invalidation_reason,
            "user_notes": review.user_notes if review else None,
            "psychology": review.psychology_before if review else None,
            "created_at": o.created_at,
            "closed_at": o.closed_at,
        }
        rows.append(row)

    if format == "csv":
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    return rows
