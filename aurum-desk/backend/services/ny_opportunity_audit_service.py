"""
Aurum Desk V14.3 - NY opportunity audit (small storage + read model).

Purpose: explain WHY a NY session produced few/no PAPER fills, without inflating counts:
  * scan ticks (every ~6s) are counted in NYSessionAudit.scan_count - they are NOT opportunities;
  * a unique opportunity = session + family + candidate identity (evidence anchor);
  * the funnel is candidates -> financially eligible -> armed -> filled (blockers never multiply candidates);
  * one missed summary per session (summary_written_at is set once, so a restart never rewrites it).

All write helpers only add/flush to the caller's session; the caller owns commit/rollback.
No secrets are stored. No orders are created here.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

import models
from services.ny_quota_helpers import classify_reason, primary_reason, CATEGORY_PRIORITY

MAX_REVISIONS = 20


def _loads(text: Optional[str], default: Any):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _typed(codes: Iterable[str]) -> List[Dict[str, str]]:
    out = []
    for c in dict.fromkeys(c for c in codes if c):
        cat, msg = classify_reason(c)
        out.append({"code": c, "category": cat, "message_vi": msg})
    return out


class NYOpportunityAuditService:
    # ------------------------------------------------------------------ session level
    @staticmethod
    def get_or_create_session(db, session_instance_id: str, session_date_ny: Optional[str],
                              symbol: str, now_ms: int) -> models.NYSessionAudit:
        row = db.query(models.NYSessionAudit).filter(
            models.NYSessionAudit.session_instance_id == session_instance_id).first()
        if row is None:
            row = models.NYSessionAudit(
                session_instance_id=session_instance_id, session_date_ny=session_date_ny, symbol=symbol,
                scan_count=0, fallback_search_scans=0, data_issue_scans=0,
                blockers_json="{}", first_scan_at=now_ms, last_scan_at=now_ms, updated_at=now_ms,
            )
            db.add(row)
            db.flush()
        return row

    @staticmethod
    def record_scan(db, *, session_instance_id: str, session_date_ny: Optional[str], symbol: str, now_ms: int,
                    quota_state: Optional[str], searched: bool, data_issue: bool,
                    primary_code: Optional[str], system_error: Optional[str] = None) -> models.NYSessionAudit:
        """Count ONE scan tick. `primary_code` is the stable primary blocker of this tick (or None)."""
        row = NYOpportunityAuditService.get_or_create_session(db, session_instance_id, session_date_ny, symbol, now_ms)
        row.scan_count = (row.scan_count or 0) + 1
        if searched:
            row.fallback_search_scans = (row.fallback_search_scans or 0) + 1
        if data_issue:
            row.data_issue_scans = (row.data_issue_scans or 0) + 1
        row.last_quota_state = quota_state
        row.last_scan_at = now_ms
        row.updated_at = now_ms
        if primary_code:
            blockers = _loads(row.blockers_json, {})
            blockers[primary_code] = int(blockers.get(primary_code, 0)) + 1
            row.blockers_json = json.dumps(blockers, sort_keys=True)
        if system_error:
            row.last_system_error = system_error[:1000]
        db.flush()
        return row

    # ------------------------------------------------------------------ candidate level
    @staticmethod
    def record_candidate(db, *, session_instance_id: str, session_date_ny: Optional[str], symbol: str,
                         family: str, candidate_key: str, direction: Optional[str], now_ms: int,
                         reasons: Optional[Iterable[str]], is_financially_eligible: bool,
                         evidence: Optional[Dict[str, Any]] = None, fingerprint: Optional[str] = None,
                         outcome: Optional[str] = None, watch_setup_id: Optional[str] = None,
                         count_seen: bool = True) -> Dict[str, Any]:
        """Upsert ONE unique opportunity. Returns {"row", "created", "revised"}.

        `reasons=None` means "no new information about blockers": existing blockers are kept and never
        compared, so a scan that only re-observes the candidate cannot flip revisions back and forth.
        """
        keep_reasons = reasons is None
        reasons = [] if keep_reasons else list(dict.fromkeys(r for r in reasons if r))
        row = db.query(models.NYOpportunityAudit).filter(
            models.NYOpportunityAudit.session_instance_id == session_instance_id,
            models.NYOpportunityAudit.strategy_family == family,
            models.NYOpportunityAudit.candidate_key == candidate_key,
        ).first()
        primary = primary_reason(reasons)
        category = classify_reason(primary)[0] if primary else None
        if row is None:
            row = models.NYOpportunityAudit(
                session_instance_id=session_instance_id, session_date_ny=session_date_ny, symbol=symbol,
                strategy_family=family, candidate_key=candidate_key, direction=direction,
                stage="FINANCIAL_ELIGIBLE" if is_financially_eligible else "STRATEGY_CANDIDATE",
                outcome=outcome, primary_reason_code=primary, reason_category=category,
                reasons_json=json.dumps(_typed(reasons)), evidence_json=json.dumps(evidence or {}, sort_keys=True),
                fingerprint=fingerprint, revision=1, revisions_json="[]",
                is_financially_eligible=bool(is_financially_eligible), was_armed=False, was_filled=False,
                watch_setup_id=watch_setup_id, seen_count=1, first_seen_at=now_ms, last_seen_at=now_ms,
            )
            db.add(row)
            db.flush()
            return {"row": row, "created": True, "revised": False}

        if count_seen:
            row.seen_count = (row.seen_count or 0) + 1
        row.last_seen_at = now_ms
        revised = False
        old_codes = [r["code"] for r in _loads(row.reasons_json, [])]
        if keep_reasons:
            reasons = old_codes
            primary = row.primary_reason_code
            category = row.reason_category
        material = (
            (fingerprint is not None and fingerprint != row.fingerprint)
            or sorted(old_codes) != sorted(reasons)
            or (bool(is_financially_eligible) and not bool(row.is_financially_eligible))
        )
        if material:
            revisions = _loads(row.revisions_json, [])
            revisions.append({"at": now_ms, "revision": (row.revision or 1) + 1,
                              "reasons": reasons, "fingerprint": fingerprint,
                              "eligible": bool(is_financially_eligible)})
            row.revisions_json = json.dumps(revisions[-MAX_REVISIONS:])
            row.revision = (row.revision or 1) + 1
            row.reasons_json = json.dumps(_typed(reasons))
            row.primary_reason_code = primary
            row.reason_category = category
            if fingerprint is not None:
                row.fingerprint = fingerprint
            if evidence is not None:
                row.evidence_json = json.dumps(evidence, sort_keys=True)
            revised = True
        if is_financially_eligible:
            row.is_financially_eligible = True
            if row.stage == "STRATEGY_CANDIDATE":
                row.stage = "FINANCIAL_ELIGIBLE"
        if outcome:
            row.outcome = outcome
        if watch_setup_id:
            row.watch_setup_id = watch_setup_id
        db.flush()
        return {"row": row, "created": False, "revised": revised}

    @staticmethod
    def mark_stage(db, *, session_instance_id: str, family: str, candidate_key: str,
                   armed: Optional[bool] = None, filled: Optional[bool] = None,
                   order_id: Optional[str] = None, outcome: Optional[str] = None, now_ms: int = 0) -> Optional[models.NYOpportunityAudit]:
        row = db.query(models.NYOpportunityAudit).filter(
            models.NYOpportunityAudit.session_instance_id == session_instance_id,
            models.NYOpportunityAudit.strategy_family == family,
            models.NYOpportunityAudit.candidate_key == candidate_key,
        ).first()
        if row is None:
            return None
        if armed:
            row.was_armed = True
            if row.stage in ("STRATEGY_CANDIDATE", "FINANCIAL_ELIGIBLE"):
                row.stage = "ARMED"
        if filled:
            row.was_filled = True
            row.stage = "FILLED"
        if order_id:
            row.order_id = order_id
        if outcome:
            row.outcome = outcome
        if now_ms:
            row.last_seen_at = now_ms
        db.flush()
        return row

    # ------------------------------------------------------------------ funnel / summary
    @staticmethod
    def funnel(db, session_instance_id: str) -> Dict[str, int]:
        rows = db.query(models.NYOpportunityAudit).filter(
            models.NYOpportunityAudit.session_instance_id == session_instance_id).all()
        order_ids = [r.order_id for r in rows if r.order_id]
        opened = set()
        if order_ids:
            opened = {o.id for o in db.query(models.PaperOrder).filter(
                models.PaperOrder.id.in_(order_ids), models.PaperOrder.opened_at.isnot(None)).all()}
        return {
            "unique_candidates": len(rows),
            "financially_eligible": sum(1 for r in rows if r.is_financially_eligible),
            "armed": sum(1 for r in rows if r.was_armed),
            # actual PAPER fills are read from the order (never from a candidate/READY flag)
            "filled": sum(1 for r in rows if r.was_filled or (r.order_id in opened)),
        }

    @staticmethod
    def _top_blockers(blockers: Dict[str, int], limit: int = 5) -> List[Dict[str, Any]]:
        items = sorted(blockers.items(),
                       key=lambda kv: (-kv[1], CATEGORY_PRIORITY.index(classify_reason(kv[0])[0]), kv[0]))
        out = []
        for code, count in items[:limit]:
            cat, msg = classify_reason(code)
            out.append({"code": code, "category": cat, "message_vi": msg, "count": count})
        return out

    @staticmethod
    def classify_missed(*, scans: int, data_issue_scans: int, funnel: Dict[str, int], fills: int,
                        top: List[Dict[str, Any]], last_system_error: Optional[str]) -> Dict[str, Optional[str]]:
        """primary missed reason + category. Missing data is DATA_UNAVAILABLE, never 'no edge'."""
        if fills > 0:
            return {"missed_category": "FILLED", "primary_missed_reason": None}
        if scans <= 0:
            return {"missed_category": "DATA_UNAVAILABLE", "primary_missed_reason": "NO_SCAN_COVERAGE"}
        if last_system_error and funnel["unique_candidates"] == 0 and not top:
            return {"missed_category": "SYSTEM", "primary_missed_reason": "SYSTEM_ERROR"}
        if funnel["unique_candidates"] == 0:
            if data_issue_scans * 2 >= scans:
                code = next((t["code"] for t in top if t["category"] == "DATA"), "FALLBACK_DATA_MISSING")
                return {"missed_category": "DATA_UNAVAILABLE", "primary_missed_reason": code}
            code = top[0]["code"] if top else "NO_CANDIDATE"
            return {"missed_category": "NO_SIGNAL", "primary_missed_reason": code}
        if funnel["armed"] == 0:
            code = top[0]["code"] if top else "ARM_BLOCKED"
            return {"missed_category": "SIGNAL_BLOCKED", "primary_missed_reason": code}
        code = top[0]["code"] if top else "FILL_REJECTED"
        return {"missed_category": "ARMED_NOT_FILLED", "primary_missed_reason": code}

    @staticmethod
    def finalize_session_summary(db, session_instance_id: str, now_ms: int) -> Optional[Dict[str, Any]]:
        """Write the missed/coverage summary ONCE after the window closed. Idempotent across restarts."""
        row = db.query(models.NYSessionAudit).filter(
            models.NYSessionAudit.session_instance_id == session_instance_id).first()
        if row is None or row.summary_written_at is not None:
            return None
        quota = db.query(models.SessionQuota).filter(
            models.SessionQuota.session_instance_id == session_instance_id).first()
        funnel = NYOpportunityAuditService.funnel(db, session_instance_id)
        blockers = _loads(row.blockers_json, {})
        top = NYOpportunityAuditService._top_blockers(blockers)
        fills = int(quota.total_fills) if quota else 0
        verdict = NYOpportunityAuditService.classify_missed(
            scans=row.scan_count or 0, data_issue_scans=row.data_issue_scans or 0, funnel=funnel,
            fills=fills, top=top, last_system_error=row.last_system_error)
        summary = {
            "session_instance_id": session_instance_id,
            "coverage": {"scans": row.scan_count or 0, "fallback_search_scans": row.fallback_search_scans or 0,
                         "data_issue_scans": row.data_issue_scans or 0,
                         "first_scan_at": row.first_scan_at, "last_scan_at": row.last_scan_at},
            "funnel": {**funnel, "filled_quota": fills},
            "top_blockers": top,
            "last_system_error": row.last_system_error,
            **verdict,
        }
        row.summary_json = json.dumps(summary, sort_keys=True)
        row.summary_written_at = now_ms
        row.primary_missed_reason = verdict["primary_missed_reason"]
        row.missed_category = verdict["missed_category"]
        row.updated_at = now_ms
        db.flush()
        return summary

    # ------------------------------------------------------------------ read model (GET only)
    @staticmethod
    def build_diagnostics(db, *, session_instance_id: str, session_date_ny: Optional[str] = None) -> Dict[str, Any]:
        """Typed read model. Legacy sessions with no audit return availability UNAVAILABLE + nulls, never fake zeros."""
        row = db.query(models.NYSessionAudit).filter(
            models.NYSessionAudit.session_instance_id == session_instance_id).first()
        quota = db.query(models.SessionQuota).filter(
            models.SessionQuota.session_instance_id == session_instance_id).first()
        base: Dict[str, Any] = {
            "session_instance_id": session_instance_id,
            "session_date_ny": session_date_ny or (quota.session_date_ny if quota else None),
            "quota": ({"status": quota.quota_status, "target": quota.target_fills, "total_fills": quota.total_fills,
                       "standard_fills": quota.standard_fills, "fallback_fills": quota.fallback_fills,
                       "reason": quota.last_status_reason} if quota else None),
        }
        if row is None:
            return {**base, "availability": "UNAVAILABLE", "scans": None, "unique_candidates": None,
                    "financially_eligible": None, "armed": None, "filled": None, "top_blockers": [],
                    "primary_missed_reason": None, "missed_category": None, "last_system_error": None,
                    "summary_written": False, "coverage": None, "freshness": None, "candidates": []}
        funnel = NYOpportunityAuditService.funnel(db, session_instance_id)
        top = NYOpportunityAuditService._top_blockers(_loads(row.blockers_json, {}))
        fills = int(quota.total_fills) if quota else 0
        verdict = NYOpportunityAuditService.classify_missed(
            scans=row.scan_count or 0, data_issue_scans=row.data_issue_scans or 0, funnel=funnel,
            fills=fills, top=top, last_system_error=row.last_system_error)
        cands = db.query(models.NYOpportunityAudit).filter(
            models.NYOpportunityAudit.session_instance_id == session_instance_id
        ).order_by(models.NYOpportunityAudit.first_seen_at.asc()).limit(10).all()
        return {
            **base,
            "availability": "MEASURED",
            "scans": row.scan_count or 0,
            "unique_candidates": funnel["unique_candidates"],
            "financially_eligible": funnel["financially_eligible"],
            "armed": funnel["armed"],
            "filled": funnel["filled"],
            "top_blockers": top,
            "primary_missed_reason": row.primary_missed_reason or verdict["primary_missed_reason"],
            "missed_category": row.missed_category or verdict["missed_category"],
            "last_system_error": row.last_system_error,
            "summary_written": row.summary_written_at is not None,
            "coverage": {"fallback_search_scans": row.fallback_search_scans or 0,
                         "data_issue_scans": row.data_issue_scans or 0,
                         "first_scan_at": row.first_scan_at, "last_scan_at": row.last_scan_at},
            "freshness": {"last_scan_at": row.last_scan_at, "last_quota_state": row.last_quota_state},
            "candidates": [{
                "candidate_key": c.candidate_key, "direction": c.direction, "stage": c.stage,
                "outcome": c.outcome, "primary_reason_code": c.primary_reason_code,
                "reason_category": c.reason_category, "revision": c.revision, "seen_count": c.seen_count,
                "reasons": _loads(c.reasons_json, []),
            } for c in cands],
        }
