"""
backend/lab/smc_probability_validation.py
V14.4.1 - Isolated research CLI helpers: chronological purged split, a small cohort model, proper-score metrics.

NOT part of the live trading path. With no real dataset these functions return UNAVAILABLE / NOT_VERIFIED with
null metrics (never 0). Correctness is shown on synthetic fixtures only; that proves the code, not the strategy.
"""
from __future__ import annotations

from collections import defaultdict
from math import log
from typing import Any, Dict, List, Optional, Sequence, Tuple


def split_chronological_purged(examples: Sequence[Dict[str, Any]], *, train_frac: float = 0.6, calib_frac: float = 0.2,
                               embargo_ms: int = 0) -> Dict[str, Any]:
    """examples: {id, group (scenario/day), decision_ms, label_end_ms, ...}. Chronological train -> calibration ->
    holdout. Examples whose LABEL WINDOW crosses a boundary are purged; whole groups stay together (a group is
    assigned by its earliest example); an embargo is applied after each boundary. No random split."""
    if not examples:
        return {"train": [], "calibration": [], "holdout": [], "purged": [], "status": "UNAVAILABLE"}
    ex = sorted(examples, key=lambda e: (e["decision_ms"], str(e["id"])))
    first: Dict[Any, int] = {}
    for e in ex:
        first.setdefault(e.get("group", e["id"]), e["decision_ms"])
    ordered_groups = sorted(first, key=lambda g: first[g])
    n_g = len(ordered_groups)
    i1, i2 = int(n_g * train_frac), int(n_g * (train_frac + calib_frac))
    bucket = {g: ("train" if i < i1 else "calibration" if i < i2 else "holdout") for i, g in enumerate(ordered_groups)}
    out: Dict[str, List[Dict[str, Any]]] = {"train": [], "calibration": [], "holdout": [], "purged": []}
    starts = {b: min((first[g] for g in ordered_groups if bucket[g] == b), default=None) for b in ("train", "calibration", "holdout")}
    ends = {"train": starts["calibration"], "calibration": starts["holdout"], "holdout": None}
    for e in ex:
        b = bucket[e.get("group", e["id"])]
        boundary = ends[b]
        if boundary is not None and (e["label_end_ms"] >= boundary - 0 or e["decision_ms"] >= boundary):
            out["purged"].append(e); continue
        prev_boundary = {"train": None, "calibration": starts["calibration"], "holdout": starts["holdout"]}[b]
        if prev_boundary is not None and e["decision_ms"] < prev_boundary + embargo_ms:
            out["purged"].append(e); continue
        out[b].append(e)
    out["status"] = "OK"
    return out


def fit_probability_model(train: Sequence[Dict[str, Any]], *, cohort_keys: Sequence[str] = ("family", "session", "regime"),
                          min_cohort_n: int = 30) -> Dict[str, Any]:
    """Empirical cohort model (target = TP_FIRST vs SL_FIRST). Needs both classes overall and per cohort.
    Returns UNAVAILABLE when there is nothing honest to fit. Fit on TRAIN only."""
    usable = [e for e in train if e.get("label") in ("TP_FIRST", "SL_FIRST")]
    classes = {e["label"] for e in usable}
    if len(usable) < min_cohort_n or classes != {"TP_FIRST", "SL_FIRST"}:
        return {"status": "UNAVAILABLE", "reason": "MISSING_CLASS_OR_SAMPLE", "cohorts": {}, "n": len(usable)}
    buckets: Dict[str, List[int]] = defaultdict(list)
    for e in usable:
        key = "|".join(str(e.get(k)) for k in cohort_keys)
        buckets[key].append(1 if e["label"] == "TP_FIRST" else 0)
    global_p = sum(1 if e["label"] == "TP_FIRST" else 0 for e in usable) / len(usable)
    cohorts = {k: {"n": len(v), "p": sum(v) / len(v)} for k, v in buckets.items() if len(v) >= min_cohort_n}
    return {"status": "FITTED", "cohorts": cohorts, "global_p": global_p, "n": len(usable), "cohort_keys": list(cohort_keys)}


def predict(model: Dict[str, Any], example: Dict[str, Any]) -> Optional[float]:
    if model.get("status") != "FITTED":
        return None
    key = "|".join(str(example.get(k)) for k in model["cohort_keys"])
    c = model["cohorts"].get(key)
    return c["p"] if c else None            # out-of-distribution cohort -> null


def evaluate_probability_model(model: Dict[str, Any], holdout: Sequence[Dict[str, Any]], *, bins: int = 5) -> Dict[str, Any]:
    """Brier + logloss + reliability bins + coverage on HOLDOUT. Brier alone is not calibration evidence."""
    pairs: List[Tuple[float, int]] = []
    for e in holdout:
        if e.get("label") not in ("TP_FIRST", "SL_FIRST"):
            continue
        p = predict(model, e)
        if p is not None:
            pairs.append((p, 1 if e["label"] == "TP_FIRST" else 0))
    total = len([e for e in holdout if e.get("label") in ("TP_FIRST", "SL_FIRST")])
    if not pairs:
        return {"status": "NOT_VERIFIED", "n": 0, "brier": None, "logloss": None, "reliability_bins": [],
                "coverage": None if total == 0 else 0.0}
    eps = 1e-9
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    ll = -sum(y * log(max(p, eps)) + (1 - y) * log(max(1 - p, eps)) for p, y in pairs) / len(pairs)
    rel = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if sel:
            rel.append({"bin": [lo, hi], "n": len(sel), "mean_p": sum(p for p, _ in sel) / len(sel),
                        "observed": sum(y for _, y in sel) / len(sel)})
    return {"status": "EVALUATED", "n": len(pairs), "brier": brier, "logloss": ll, "reliability_bins": rel,
            "coverage": len(pairs) / total if total else None}
