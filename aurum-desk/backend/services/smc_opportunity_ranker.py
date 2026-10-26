"""
backend/services/smc_opportunity_ranker.py
V14.4.1 - Ordering of ALREADY confirmed opportunities. Eligibility first, ranking second.

1. Gate (no ranking can override): certified SMC chain + exact binding + real target + valid Net R:R + common guards.
2. Rank by validated expected net utility when available; otherwise by STRUCTURAL strength only, and the label says
   "chưa có thống kê". A checklist count is never presented as a winrate; a high predicted P never beats a red
   news/lesson/limit block.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence


def _eligible(c: Dict[str, Any]) -> bool:
    return (c.get("chain_verified") is True and c.get("binding_verified") is True and c.get("has_target") is True
            and c.get("plan_valid") is True and (c.get("net_rr") or 0) > 0 and not c.get("guard_blockers"))


def rank_confirmed_opportunities(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each candidate: {id, chain_verified, binding_verified, has_target, plan_valid, net_rr, guard_blockers[],
    structural_score, ev_conservative_net_r?}. Returns ranked entries with a rank_basis label."""
    ranked = []
    for c in candidates:
        ok = _eligible(c)
        ev = c.get("ev_conservative_net_r")
        ranked.append({
            "id": c.get("id"), "eligible": ok,
            "blockers": list(c.get("guard_blockers") or []) + ([] if ok or c.get("guard_blockers") else ["NOT_CERTIFIED_OR_NO_TARGET"]),
            "rank_basis": "VALIDATED_EXPECTED_NET_UTILITY" if (ok and ev is not None) else "STRUCTURAL_ONLY_NO_STATISTICS",
            "utility": ev if (ok and ev is not None) else None,
            "structural_score": c.get("structural_score"),
            "label_vi": ("Xếp hạng theo kỳ vọng ròng đã kiểm định" if (ok and ev is not None)
                         else "Xếp hạng theo cấu trúc, chưa có thống kê"),
        })
    ranked.sort(key=lambda r: (not r["eligible"],
                               -(r["utility"] if r["utility"] is not None else float("-inf")),
                               -(r["structural_score"] or 0), str(r["id"])))
    return ranked
