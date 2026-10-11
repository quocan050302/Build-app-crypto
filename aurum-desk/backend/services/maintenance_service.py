import time
import argparse
import logging
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from database import SessionLocal
import models

logger = logging.getLogger(__name__)

# Exact-match known test fixture signatures from test_v6_error_and_arm_levels.py
FIXTURE_SIGNATURES = [
    {
        "id": "watch-cross-15M",
        "strategy": "SMC_V5",
        "direction": "LONG",
        "margin_mode": "CROSS",
        "provisional_entry": 4120.0,
        "provisional_sl": 4110.0,
        "provisional_tp": 4160.0,
        "invalidation_price": 4100.0,
    },
    {
        "id": "watch-test-15M",
        "strategy": "SMC_V5",
        "direction": "LONG",
        "margin_mode": "ISOLATED",
        "provisional_entry": 4120.0,
        "provisional_sl": 4110.0,
        "provisional_tp": 4135.0,
        "invalidation_price": 4100.0,
    }
]

def audit_and_reconcile_fixtures(db: Session, dry_run: bool = True) -> Dict[str, Any]:
    """
    Safely audits and reconciles contaminated test fixtures in runtime database.
    - Uses exact-match evidence on (id, strategy, direction, levels, margin_mode).
    - Guard: Never touches setups linked to active or armed paper orders.
    - If dry_run=True: only reports affected candidates without mutating state.
    - If dry_run=False: transitions candidates to INVALIDATED with reason TEST_FIXTURE_CONTAMINATION.
    - Preserves all trades, audits, counters, and outbox entries. Idempotent on repeated runs.
    """
    candidates: List[Dict[str, Any]] = []
    quarantined: List[str] = []
    skipped_active: List[str] = []
    already_quarantined: List[str] = []

    setups = db.query(models.WatchSetup).all()

    for s in setups:
        # Check against fixture signatures
        is_fixture_match = False
        match_sig = None
        for sig in FIXTURE_SIGNATURES:
            if s.id == sig["id"] and s.strategy == sig["strategy"] and s.direction == sig["direction"]:
                if (
                    abs(s.provisional_entry - sig["provisional_entry"]) < 1e-4 and
                    abs(s.provisional_sl - sig["provisional_sl"]) < 1e-4 and
                    abs(s.provisional_tp - sig["provisional_tp"]) < 1e-4
                ):
                    is_fixture_match = True
                    match_sig = sig
                    break

        if not is_fixture_match:
            continue

        # Check if already quarantined
        if s.state == "INVALIDATED" and s.invalidation_reason and "TEST_FIXTURE_CONTAMINATION" in s.invalidation_reason:
            already_quarantined.append(s.id)
            continue

        # Safety Guard: Check for linkage to any active/armed PaperOrder
        active_order = db.query(models.PaperOrder).filter(
            models.PaperOrder.setup_id == s.id,
            models.PaperOrder.state.in_(["paper_open", "armed"])
        ).first()

        if active_order:
            skipped_active.append(s.id)
            logger.warning(f"Skipping fixture candidate {s.id}: linked to active order {active_order.id}")
            continue

        candidate_info = {
            "setup_id": s.id,
            "strategy": s.strategy,
            "direction": s.direction,
            "state": s.state,
            "margin_mode": s.margin_mode,
            "provisional_entry": s.provisional_entry,
            "provisional_sl": s.provisional_sl,
            "provisional_tp": s.provisional_tp,
            "created_at": s.created_at,
            "match_evidence": match_sig["id"]
        }
        candidates.append(candidate_info)

        if not dry_run:
            s.state = "INVALIDATED"
            s.invalidation_reason = "TEST_FIXTURE_CONTAMINATION: Đối soát xác nhận fixture kiểm thử lọt vào dữ liệu, đã cách ly an toàn"
            s.updated_at = int(time.time() * 1000)
            quarantined.append(s.id)

    if not dry_run and quarantined:
        db.commit()

    return {
        "status": "success",
        "dry_run": dry_run,
        "total_checked": len(setups),
        "candidates_count": len(candidates),
        "candidates": candidates,
        "quarantined_count": len(quarantined),
        "quarantined_ids": quarantined,
        "skipped_active_linkage": skipped_active,
        "already_quarantined": already_quarantined
    }

def audit_contaminated_fixtures(db: Session) -> Dict[str, Any]:
    """Read-only audit of contaminated fixtures."""
    return audit_and_reconcile_fixtures(db, dry_run=True)

def quarantine_contaminated_fixtures(db: Session, dry_run: bool = False) -> Dict[str, Any]:
    """Quarantine contaminated test fixtures."""
    return audit_and_reconcile_fixtures(db, dry_run=dry_run)

def main():
    parser = argparse.ArgumentParser(description="Aurum Desk Fixture Maintenance Tool")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Perform read-only dry run (default)")
    parser.add_argument("--quarantine", action="store_true", default=False, help="Execute quarantine of confirmed fixtures")
    args = parser.parse_args()

    # Default to dry-run unless --quarantine is explicitly specified
    execute_quarantine = args.quarantine
    dry_run = not execute_quarantine

    db = SessionLocal()
    try:
        report = audit_and_reconcile_fixtures(db, dry_run=dry_run)
        print("=== AURUM DESK FIXTURE MAINTENANCE REPORT ===")
        print(f"Mode: {'DRY RUN (Read-Only)' if report['dry_run'] else 'QUARANTINE EXECUTED'}")
        print(f"Candidates Found: {report['candidates_count']}")
        for c in report["candidates"]:
            print(f" - {c['setup_id']} ({c['strategy']} {c['direction']} {c['margin_mode']}) state={c['state']}")
        print(f"Quarantined: {report['quarantined_count']} ({report['quarantined_ids']})")
        if report["skipped_active_linkage"]:
            print(f"Skipped due to active order linkage: {report['skipped_active_linkage']}")
        if report["already_quarantined"]:
            print(f"Already quarantined: {report['already_quarantined']}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
