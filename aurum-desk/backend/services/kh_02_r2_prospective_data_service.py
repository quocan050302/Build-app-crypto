"""
aurum-desk/backend/services/kh_02_r2_prospective_data_service.py

AURUM DESK — KH-02.R2 Prospective OOS Data Ingestion & Escrow Readiness Service
SPEC VERSION: KH-02.R2-v1.0 | PURE DATA/AUDIT ONLY

Strictly additive, read-only with respect to all prior KH-01* / KH-02.R1 escrows and datasets.
Zero order/exchange execution calls, zero wallet/API-key reads, zero OOS peeking (`OOS_EVALUATIONS == 0`).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_01b_r1_entry_edge_discovery_service import (
    build_and_validate_future_oos_escrow_registry,
)
from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2 = "KH-02.R2-v1.0"
SCHEMA_VERSION_KH02_R2 = "kh_02_r2_research_integrity_v1"
PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2 = 240
PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2 = PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2 * 60_000

PRIOR_ESCROW_READONLY_PATHS: Tuple[str, ...] = (
    "aurum-desk/docs/artifacts/kh_01b_r1/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_data_safety/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_ram_safety_r3/latest_run.json",
    "aurum-desk/docs/artifacts/kh_02_entry_foundation/latest_run.json",
)

PROSPECTIVE_SNAPSHOT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "snapshot_id",
    "schema_version",
    "exchange",
    "exchange_contract_id",
    "symbol",
    "price_precision",
    "qty_precision",
    "timeframe",
    "start_utc_ms",
    "end_utc_ms",
    "bar_count",
    "utc_monotonic_strict",
    "observed_bid_ask_present",
    "quote_mode",
    "raw_response_sha256",
    "pagination_lineage",
    "source_signature_or_checksum",
    "capture_time_ms",
    "known_at_ms",
    "dataset_sha256",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_prospective_snapshot_entry_r2(
    entry: Mapping[str, Any],
    *,
    freeze_timestamp_ms: int,
    known_dev_shas: Sequence[str],
    embargo_ms: int = PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2,
) -> Dict[str, Any]:
    """
    Validates a single candidate append-only prospective OOS raw snapshot entry without
    ever evaluating any strategy on it (`OOS_EVALUATIONS == 0`).
    """
    violations: List[str] = []
    missing = [k for k in PROSPECTIVE_SNAPSHOT_REQUIRED_FIELDS if k not in entry or entry.get(k) is None]
    if missing:
        violations.append(f"MISSING_REQUIRED_SNAPSHOT_FIELDS:{','.join(missing)}")

    ds_sha = str(entry.get("dataset_sha256") or "").strip().lower()
    raw_sha = str(entry.get("raw_response_sha256") or "").strip().lower()
    dev_set = {str(x).strip().lower() for x in known_dev_shas if x}

    if len(ds_sha) != 64:
        violations.append("INVALID_DATASET_SHA256_LENGTH")
    if len(raw_sha) != 64:
        violations.append("INVALID_RAW_RESPONSE_SHA256_LENGTH")
    if ds_sha in dev_set:
        violations.append(f"REUSED_DEV_DATASET_BLOCKED_AS_OOS:{ds_sha}")

    start_ms = int(entry.get("start_utc_ms") or 0)
    end_ms = int(entry.get("end_utc_ms") or 0)
    capture_ms = int(entry.get("capture_time_ms") or 0)
    known_ms = int(entry.get("known_at_ms") or 0)
    bar_count = int(entry.get("bar_count") or 0)

    if bar_count <= 0:
        violations.append("EMPTY_SNAPSHOT_BAR_COUNT")
    if start_ms <= 0 or end_ms <= start_ms:
        violations.append("INVALID_UTC_START_END_RANGE")
    if not bool(entry.get("utc_monotonic_strict", False)):
        violations.append("NON_MONOTONIC_UTC_TIMESTAMPS")
    if start_ms < int(freeze_timestamp_ms) + int(embargo_ms):
        violations.append(
            f"EMBARGO_VIOLATION:start_utc_ms={start_ms}<freeze_plus_embargo={int(freeze_timestamp_ms) + int(embargo_ms)}"
        )
    if capture_ms < end_ms or known_ms < end_ms:
        violations.append("CAUSALITY_VIOLATION_CAPTURE_OR_KNOWN_BEFORE_END_UTC")

    quote_mode = str(entry.get("quote_mode") or "").upper()
    obs_bbo = bool(entry.get("observed_bid_ask_present", False))
    if obs_bbo and quote_mode != "OBSERVED_L1_BBO":
        violations.append(f"INCONSISTENT_QUOTE_MODE_FOR_OBSERVED_BBO:{quote_mode}")
    if (not obs_bbo) and quote_mode == "OBSERVED_L1_BBO":
        violations.append("CLAIMED_OBSERVED_L1_BBO_WITHOUT_OBSERVED_FLAG")
    if bool(entry.get("synthetic_generated", False)):
        violations.append("SYNTHETIC_DATA_FORBIDDEN_AS_PROSPECTIVE_OOS")
    if int(entry.get("strategy_evaluation_count", 0)) != 0:
        violations.append("NONZERO_STRATEGY_EVALUATIONS_ON_PROSPECTIVE_SNAPSHOT")

    return {
        "snapshot_id": str(entry.get("snapshot_id") or ""),
        "valid_for_escrow_admission": len(violations) == 0,
        "violations": violations,
        "embargo_cutoff_ms": int(freeze_timestamp_ms) + int(embargo_ms),
        "dataset_sha256": ds_sha,
        "observed_bid_ask_present": obs_bbo,
    }


def prepare_r2_prospective_oos_ingestion_and_escrow(
    *,
    repo_root: Path,
    freeze_timestamp_ms: int,
    tested_executable_sha: str,
    frozen_config_sha256: str,
    proposed_prospective_snapshots: Sequence[Mapping[str, Any]] = (),
    known_dev_shas: Sequence[str] = (
        CANONICAL_DATASET_SHA256_KH02,
        KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    ),
    claimed_oos_evaluations: int = 0,
    disk_free_gib: Optional[float] = None,
    min_disk_gib_for_capture: float = 20.5,
    network_capture_authorized: bool = False,
) -> Dict[str, Any]:
    """
    §03(12): Read-only audit of existing KH-01B/DSC/KH-02.R1 escrows, validation of any
    append-only candidate prospective snapshots under strict 240-min embargo + non-overlap,
    and strict enforcement of `OOS_EVALUATIONS == 0` (READINESS ONLY — never peeks at OOS).

    Fail-closed: when no verified new prospective dataset is present (or network/disk prevents
    safe capture), returns `FUTURE_OOS_READINESS_VERDICT = "BLOCKED_NO_VERIFIED_NEW_DATA"`
    and `FRESH_OOS_VERDICT = "BLOCKED_NO_UNTOUCHED_DATA"` without fabricating data.
    """
    repo_root = Path(repo_root).resolve()
    prior_escrow_audit: List[Dict[str, Any]] = []
    prior_violations: List[str] = []

    for rel_p in PRIOR_ESCROW_READONLY_PATHS:
        full_p = repo_root / rel_p
        if full_p.is_file():
            sha = _sha256_file(full_p)
            try:
                doc = json.loads(full_p.read_text(encoding="utf-8"))
                prior_escrow_audit.append(
                    {
                        "path": rel_p,
                        "exists": True,
                        "sha256": sha,
                        "run_id": doc.get("run_id"),
                        "mutated_by_r2": False,
                    }
                )
            except Exception as exc:
                prior_escrow_audit.append({"path": rel_p, "exists": True, "sha256": sha, "parse_error": str(exc)})
                prior_violations.append(f"PRIOR_POINTER_UNREADABLE:{rel_p}")
        else:
            prior_escrow_audit.append({"path": rel_p, "exists": False, "sha256": None, "mutated_by_r2": False})

    # Validate candidate prospective snapshots (if any supplied by operator)
    snapshot_validations: List[Dict[str, Any]] = []
    valid_escrow_proposals: List[Dict[str, Any]] = []
    integrity_violations: List[str] = []

    for raw_snap in proposed_prospective_snapshots:
        v = validate_prospective_snapshot_entry_r2(
            raw_snap,
            freeze_timestamp_ms=freeze_timestamp_ms,
            known_dev_shas=known_dev_shas,
        )
        snapshot_validations.append(v)
        if v["valid_for_escrow_admission"]:
            valid_escrow_proposals.append(
                {
                    "dataset_id": str(raw_snap["snapshot_id"]),
                    "snapshot_id": str(raw_snap["snapshot_id"]),
                    "dataset_sha256": str(raw_snap["dataset_sha256"]),
                    "earliest_bar_open_ms": int(raw_snap["start_utc_ms"]),
                    "latest_bar_close_ms": int(raw_snap["end_utc_ms"]),
                    "bar_count": int(raw_snap["bar_count"]),
                    "partition_role": "FRESH_UNTOUCHED",
                    "source_kind": "PROSPECTIVE_OHLCV_AND_L1_BBO",
                    "strategy_evaluation_count": 0,
                    "used_for_entry_selection": False,
                    "previously_inspected": False,
                    "is_single_bbo_snapshot": False,
                    "capture_time_ms": int(raw_snap["capture_time_ms"]),
                    "known_at_ms": int(raw_snap["known_at_ms"]),
                    "observed_bid_ask_present": bool(raw_snap["observed_bid_ask_present"]),
                }
            )
        else:
            integrity_violations.extend(v["violations"])

    # Reuse authoritative read-only KH-01B.R1 escrow registry validator
    escrow_registry = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=freeze_timestamp_ms,
        frozen_candidate_code_sha=tested_executable_sha,
        frozen_config_sha256=frozen_config_sha256,
        proposed_oos_entries=valid_escrow_proposals,
        known_dev_dataset_shas=known_dev_shas,
    )
    escrow_violations = list(escrow_registry.get("violations") or [])
    integrity_violations.extend(escrow_violations)

    if int(claimed_oos_evaluations) != 0:
        integrity_violations.append(f"UNAUTHORIZED_OOS_PEEK_OR_EVALUATION:claimed={claimed_oos_evaluations}")
    if int(escrow_registry.get("strategy_evaluation_count_total", 0)) != 0:
        integrity_violations.append("ESCROW_NONZERO_STRATEGY_EVALUATIONS")

    total_oos_evals = int(claimed_oos_evaluations) + int(escrow_registry.get("strategy_evaluation_count_total", 0))
    disk_blocks_capture = disk_free_gib is not None and float(disk_free_gib) < float(min_disk_gib_for_capture)

    if integrity_violations:
        readiness_verdict = "FAIL"
        fresh_oos_verdict = "FAIL"
    elif len(valid_escrow_proposals) > 0:
        readiness_verdict = "SEALED_PROSPECTIVE_UNTOUCHED_NOT_EVALUATED"
        fresh_oos_verdict = "SEALED_UNTOUCHED_NOT_EVALUATED"
    else:
        readiness_verdict = "BLOCKED_NO_VERIFIED_NEW_DATA"
        fresh_oos_verdict = "BLOCKED_NO_UNTOUCHED_DATA"

    capture_block_reasons: List[str] = []
    if not network_capture_authorized:
        capture_block_reasons.append("OFFLINE_READINESS_RUN_NO_LIVE_EXCHANGE_CAPTURE_REQUESTED")
    if disk_blocks_capture:
        capture_block_reasons.append(
            f"LOW_DISK_FREE_GIB:{float(disk_free_gib):.4f}<{float(min_disk_gib_for_capture):.2f}"
        )
    if not valid_escrow_proposals:
        capture_block_reasons.append("NO_NEW_POST_FREEZE_VERIFIED_SNAPSHOTS_SUPPLIED")

    embargo_cutoff_ms = int(freeze_timestamp_ms) + PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2
    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "mission_scope": "READINESS_AND_ESCROW_CONTRACT_ONLY_NO_OOS_EVALUATION",
        "freeze_timestamp_ms": int(freeze_timestamp_ms),
        "freeze_timestamp_utc": _utc_iso(int(freeze_timestamp_ms)),
        "embargo_minutes": PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2,
        "embargo_cutoff_ms": embargo_cutoff_ms,
        "embargo_cutoff_utc": _utc_iso(embargo_cutoff_ms),
        "frozen_candidate_code_sha": str(tested_executable_sha),
        "frozen_config_sha256": str(frozen_config_sha256),
        "known_reused_dev_shas": sorted({str(x).strip().lower() for x in known_dev_shas if x}),
        "prior_escrow_readonly_audit": prior_escrow_audit,
        "prior_escrow_mutated_count": 0,
        "append_only_snapshot_schema_contract": {
            "required_fields": list(PROSPECTIVE_SNAPSHOT_REQUIRED_FIELDS),
            "write_once_immutable": True,
            "allow_synthetic_as_observed": False,
            "allow_reused_dev_as_oos": False,
            "required_embargo_minutes_after_freeze": PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2,
            "secrets_or_api_keys_logged": False,
        },
        "proposed_snapshots_count": len(proposed_prospective_snapshots),
        "accepted_prospective_snapshots_count": len(valid_escrow_proposals),
        "snapshot_validations": snapshot_validations,
        "future_oos_escrow_registry": escrow_registry,
        "capture_block_reasons": capture_block_reasons,
        "OOS_EVALUATIONS": total_oos_evals,
        "FUTURE_OOS_READINESS_VERDICT": readiness_verdict,
        "FRESH_OOS_VERDICT": fresh_oos_verdict,
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": {
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
        },
        "operator_next_steps_for_prospective_data": [
            "1. Free disk space to >= 25.0 GiB (strict >= 20.5 GiB preflight floor) before starting prospective market-data capture.",
            "2. Capture genuinely new post-freeze Bitget XAUUSDT M1/M15/H1 OHLCV AND real timestamped L1 BBO bid/ask quotes strictly starting >= freeze_timestamp + 240 minutes.",
            "3. Record raw HTTP response SHA256, pagination lineage, UTC monotonicity, contract precision, capture_time_ms, and known_at_ms into an append-only escrow directory without modifying historical_dataset_XAUUSDT.json.gz.",
            "4. Keep OOS_EVALUATIONS == 0 until a distinct owner-approved prospective validation protocol explicitly authorizes a single unsealing evaluation.",
        ],
        "violations": integrity_violations + prior_violations,
        "escrow_contract_integrity_status": "PASS" if not (integrity_violations or prior_violations) else "FAIL",
    }
    payload["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "artifact_canonical_sha256"}
    )
    return payload
