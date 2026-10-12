"""
backend/services/smc_probability_registry.py
V14.4.1 - Local, trusted model registry. No model ships by default, so every estimate is UNAVAILABLE.

A manifest (JSON) describes an offline-validated artifact: hash/schema, training cutoff, calibration+holdout metrics,
cost/feature versions, event/horizon, support and expiry. Only local files under the registry directory are read
(no URLs, no pickle). Out-of-distribution or unknown context -> p=None with a status, never a confident number.

The live system never "learns" from one trade: a closed order only stores a feature snapshot + outcome for the
OFFLINE batch revalidation (schedule_offline_revalidation); live weights/SL/TP/guards are not changed by it.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from services.smc_probability_types import (
    ProbabilityEstimate, STATUS_NOT_VERIFIED, STATUS_UNAVAILABLE, STATUS_VALIDATED,
)

REGISTRY_DIR = os.environ.get("AURUM_PROB_REGISTRY_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "lab", "artifacts", "prob_registry")
REQUIRED_MANIFEST_KEYS = ("model_id", "schema_version", "feature_schema", "event", "horizon", "training_cutoff_ms",
                          "calibration_metrics", "holdout_metrics", "cost_version", "support_n", "expires_at_ms",
                          "artifact_file", "artifact_sha256")

_cache: Dict[str, Any] = {}


def eligible_for_inference(manifest: Dict[str, Any], *, now_ms: int, context_features: Optional[Dict[str, Any]] = None
                           ) -> (bool, str):
    for k in REQUIRED_MANIFEST_KEYS:
        if manifest.get(k) in (None, "", {}):
            return False, f"MANIFEST_MISSING:{k}"
    if int(manifest["training_cutoff_ms"]) > now_ms:
        return False, "TRAINED_ON_THE_FUTURE"
    if int(manifest["expires_at_ms"]) <= now_ms:
        return False, "MODEL_EXPIRED"
    hm = manifest.get("holdout_metrics") or {}
    if hm.get("n") in (None, 0) or hm.get("brier") is None or hm.get("reliability_bins") in (None, []):
        return False, "HOLDOUT_NOT_VERIFIED"
    if context_features is not None and context_features.get("feature_schema") != manifest["feature_schema"]:
        return False, "FEATURE_SCHEMA_MISMATCH"
    return True, "OK"


def load_validated_model(model_id: str, *, now_ms: int, registry_dir: Optional[str] = None) -> Dict[str, Any]:
    """Returns {"status": VALIDATED|NOT_VERIFIED|UNAVAILABLE, "manifest": ..., "reason": ...}. Local files only."""
    base = os.path.abspath(registry_dir or REGISTRY_DIR)
    manifest_path = os.path.abspath(os.path.join(base, f"{model_id}.manifest.json"))
    if not manifest_path.startswith(base + os.sep) or "://" in model_id:
        return {"status": STATUS_UNAVAILABLE, "reason": "UNTRUSTED_PATH", "manifest": None}
    if not os.path.isfile(manifest_path):
        return {"status": STATUS_UNAVAILABLE, "reason": "NO_MODEL", "manifest": None}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return {"status": STATUS_NOT_VERIFIED, "reason": "MANIFEST_UNREADABLE", "manifest": None}
    ok, why = eligible_for_inference(manifest, now_ms=now_ms)
    if not ok:
        return {"status": STATUS_NOT_VERIFIED, "reason": why, "manifest": manifest}
    art = os.path.abspath(os.path.join(base, str(manifest["artifact_file"])))
    if not art.startswith(base + os.sep) or not os.path.isfile(art):
        return {"status": STATUS_NOT_VERIFIED, "reason": "ARTIFACT_MISSING", "manifest": manifest}
    with open(art, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if digest != manifest["artifact_sha256"]:
        return {"status": STATUS_NOT_VERIFIED, "reason": "ARTIFACT_HASH_MISMATCH", "manifest": manifest}
    _cache[model_id] = (digest, manifest)
    return {"status": STATUS_VALIDATED, "reason": "OK", "manifest": manifest}


def estimate_for_context(context_features: Optional[Dict[str, Any]], *, now_ms: int,
                         model_id: Optional[str] = None, registry_dir: Optional[str] = None) -> ProbabilityEstimate:
    """No registered, eligible model -> UNAVAILABLE (default today). Even with a model, only a context whose
    cohort key is covered gets a number; anything else stays null (out-of-distribution)."""
    if not model_id:
        return ProbabilityEstimate.unavailable()
    loaded = load_validated_model(model_id, now_ms=now_ms, registry_dir=registry_dir)
    if loaded["status"] != STATUS_VALIDATED:
        return ProbabilityEstimate(status=loaded["status"], note_vi="Mô hình xác suất chưa đủ điều kiện sử dụng: "
                                   f"{loaded['reason']}.")
    m = loaded["manifest"]
    cohort = (context_features or {}).get("cohort_key")
    table = (m.get("cohorts") or {})
    if cohort is None or cohort not in table:
        return ProbabilityEstimate(status=STATUS_UNAVAILABLE, event=m["event"], horizon=m["horizon"],
                                   model_version=m["model_id"], note_vi="Bối cảnh nằm ngoài vùng dữ liệu đã kiểm định.")
    row = table[cohort]
    return ProbabilityEstimate(
        status=STATUS_VALIDATED, event=m["event"], horizon=m["horizon"], p=float(row["p"]), support_n=int(row["n"]),
        model_version=m["model_id"], cohort_version=m.get("cohort_version"),
        oos_from_ms=(m.get("holdout_metrics") or {}).get("from_ms"), oos_to_ms=(m.get("holdout_metrics") or {}).get("to_ms"),
        calibration_status="CALIBRATED_ON_SEPARATE_SPLIT", uncertainty={"ci_low": row.get("ci_low"), "ci_high": row.get("ci_high")},
        note_vi="Xác suất TP trước SL trong horizon đã định, theo mô hình đã kiểm định ngoài mẫu.")


def schedule_offline_revalidation(outcome_row: Dict[str, Any]) -> Dict[str, Any]:
    """Contract: record the feature snapshot + outcome of a CLOSED order for the next OFFLINE batch. It changes
    no live weight, threshold, SL/TP or guard, and does not claim that anything was 'learned'."""
    return {"queued_for_offline_batch": True, "live_parameters_changed": False,
            "order_id": outcome_row.get("order_id"), "note": "Một lệnh không đủ để học; chờ kiểm định theo lô ngoài mẫu."}
