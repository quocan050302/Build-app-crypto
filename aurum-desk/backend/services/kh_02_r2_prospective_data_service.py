"""
aurum-desk/backend/services/kh_02_r2_prospective_data_service.py

AURUM DESK — KH-02.R2 Prospective OOS Data Ingestion & Escrow Readiness Service
SPEC VERSION: KH-02.R2-v1.0 | PURE DATA/AUDIT ONLY

Strictly additive, read-only with respect to all prior KH-01* / KH-02.R1 escrows and datasets.
Zero order/exchange execution calls, zero wallet/API-key reads, zero OOS peeking (`OOS_EVALUATIONS == 0`).
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

# Lightweight canonical constants (single source of truth shared with verifiers — zero heavy ORM imports)
CANONICAL_DATASET_SHA256_KH02 = "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256 = "2bfeae81c454a5d9d39b66358c30db014db2133a79eb091dc5e2501ff12bc38c"
CANONICAL_DATASET_REL_PATH_KH02_R2 = "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"
MANDATORY_BRANCH_KH02_R2_SHARED = "feature/v17-9-2-structural-sl-tp-from-qa"
ACTIVATION_READY_KH02_R2_SHARED = "NO"
STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED = 20.5
STRICT_RUNTIME_DISK_MIN_GIB_SHARED = 20.0
HARD_RAM_FLOOR_MIB_SHARED = 512.0
GLOBAL_MAX_DESCENDANTS_SHARED = 4

PRIOR_R2_LATEST_PTR_REL_SHARED = "aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/latest_run.json"
PRIOR_R2_LATEST_PTR_SHA256_SHARED = "284efdfec507d70266e4ea18a9e4d91cf06f325fd2c0ab0eeeb89970f2f2f013"
PRIOR_R3_LATEST_PTR_REL_SHARED = "aurum-desk/docs/artifacts/kh_01b_r1_ram_safety_r3/latest_run.json"
PRIOR_R3_LATEST_PTR_SHA256_SHARED = "3af706d98b7980120cc124e9616e18b17de4d5067121475c4e38c5408b18e283"
PRIOR_KH02_R1_RUN_ID_SHARED = "20261121T033338Z_b99ad30313a6"
PRIOR_KH02_R1_TESTED_SHA_SHARED = "b99ad30313a6ed5929ba4cbaeab4383bd3db6be5"
PRIOR_KH02_R1_LATEST_PTR_REL_SHARED = "aurum-desk/docs/artifacts/kh_02_entry_foundation/latest_run.json"
PRIOR_KH02_R1_LATEST_PTR_SHA256_SHARED = "0e97fe682c7908653ca18002236e4839863108b69ec37784b838e1e98c35e5a4"
PRIOR_KH02_R2_RUN_ID_SHARED = "20261121T154352Z_8ca245bcf4d8"
PRIOR_KH02_R2_TESTED_SHA_SHARED = "8ca245bcf4d849c44426d047dc025609259bc21d"
PRIOR_KH02_R2_LATEST_PTR_REL_SHARED = "aurum-desk/docs/artifacts/kh_02_r2_research_integrity/latest_run.json"
PRIOR_KH02_R2_LATEST_PTR_SHA256_SHARED = "3f99e10d75138914570ee25b4d3d4af1b4e0160716b8fc082062f67882f86e8f"
PRIOR_KH02_R2_MANIFEST_22_REL_SHARED = (
    "aurum-desk/docs/artifacts/kh_02_r2_research_integrity/"
    "20261121T154352Z_8ca245bcf4d8/22_independent_os_disk_verifier_sha256_manifest.json"
)
PRIOR_KH02_R2_MANIFEST_22_SHA256_SHARED = "9e7aad29cd963da17170634ea363120dcf02f92a43fa5efc8ed37048186b9169"
PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED = "1d5b82a9e0946b02e414e797f61804594645bbb5"
PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA_SHARED = "abf729f596cea4a5e42ead702a87d36dc8469fd5"
PRIOR_KH02_R2_C1_RUN_ID_SHARED = "20261122T013329Z_1d5b82a9e094"
PRIOR_KH02_R2_C1_MANIFEST_16_REL_SHARED = (
    "aurum-desk/docs/artifacts/kh_02_r2_strict_safety_oos_readiness/"
    "20261122T013329Z_1d5b82a9e094/16_independent_disk_verifier_content_sha256_manifest.json"
)
PRIOR_KH02_R2_C1_MANIFEST_16_SHA256_SHARED = "050a427e4dc6972af180be7c4ce872e656e7b202bb2369d07eeefa9439ac4b06"
PRIOR_KH02_R2_C2_TESTED_SHA_SHARED = "732a22fbdeda8458f0c982a51049fa48b3c58f4d"
PRIOR_KH02_R2_C2_EVIDENCE_SHA_SHARED = "ff8ca3e749d183757ce100ad2d7ae749e7391d86"
PRIOR_KH02_R2_C2_PUB_HEAD_SHA_SHARED = "02c3a0459b5de7b298663fb0002744b7552c7b0e"
PRIOR_KH02_R2_C2_RUN_ID_SHARED = "20261122T113807Z_732a22fbdeda"
PRIOR_KH02_R2_C2_LATEST_PTR_REL_SHARED = "aurum-desk/docs/artifacts/kh_02_r2_c2_acceptance/latest_run.json"
PRIOR_KH02_R2_C2_LATEST_PTR_SHA256_SHARED = "2aaadffc2bb3c810cd088e06e56f18c980174df6b174295499d875660d664d91"
PRIOR_KH02_R2_C2_MANIFEST_17_REL_SHARED = (
    "aurum-desk/docs/artifacts/kh_02_r2_c2_acceptance/"
    "20261122T113807Z_732a22fbdeda/17_independent_os_verifier_sha256_manifest.json"
)
PRIOR_KH02_R2_C2_MANIFEST_17_SHA256_SHARED = "70373079db7bf1802aca5947e7a62564bb03c65c873bf923942b9fd6668ab518"
R2_NEW_FILES_ALLOWLIST_SHARED: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
    "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
    "aurum-desk/tools/run_kh_02_r2_verification.py",
    "aurum-desk/tools/kh_02_r2_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_research_integrity.py",
    "aurum-desk/backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
    "aurum-desk/backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
)

SPEC_VERSION_KH02_R2 = "KH-02.R2-v1.0"
SPEC_VERSION_KH02_R2_C1 = "KH-02.R2.C1-v1.0"
SCHEMA_VERSION_KH02_R2 = "kh_02_r2_research_integrity_v1"
SCHEMA_VERSION_KH02_R2_C1 = "kh_02_r2_c1_prospective_snapshot_v1"
SCHEMA_VERSION_KH02_R2_C1_ARTIFACT = "kh_02_r2_c1_strict_safety_oos_readiness_v1"
PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2 = 240
PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2 = PROSPECTIVE_OOS_EMBARGO_MINUTES_KH02_R2 * 60_000

# Authoritative KH-02.R2 Freeze & Embargo Coordinates (from artifact 14 — NEVER silently rewrite!)
R2_AUTHORITATIVE_ARTIFACT_14_REL = (
    "aurum-desk/docs/artifacts/kh_02_r2_research_integrity/"
    "20261121T154352Z_8ca245bcf4d8/14_prospective_oos_escrow_collection_readiness_no_peek.json"
)
R2_AUTHORITATIVE_ARTIFACT_14_SHA256 = "cb22bef6d958b9f7de13fa4812a3bf72a577606aa8ea9d8db628cb71ae26c38e"
R2_AUTHORITATIVE_FROZEN_CODE_SHA = "8ca245bcf4d849c44426d047dc025609259bc21d"
R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256 = "e8255e80528e7477a92e46134257bef669128c24b6ebcabc72aefe9ae4aac421"
R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS = 1795275832732
R2_AUTHORITATIVE_FREEZE_TIMESTAMP_UTC = "2026-11-21T15:43:52Z"
R2_AUTHORITATIVE_EMBARGO_MINUTES = 240
R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS = 1795290232732
R2_AUTHORITATIVE_EMBARGO_CUTOFF_UTC = "2026-11-21T19:43:52Z"
EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1 = "INCLUSIVE_START_GE_EMBARGO_CUTOFF_MS__EXCLUSIVE_LT_CUTOFF_REJECTED"

VALID_PROSPECTIVE_DATA_ORIGINS: Tuple[str, ...] = (
    "OBSERVED_QUOTE",
    "OBSERVED_TRADE",
    "OHLCV_SNAPSHOT",
)

FORBIDDEN_SECRET_OR_ACCOUNT_SUBSTRINGS: Tuple[str, ...] = (
    "api_key",
    "apikey",
    "api_secret",
    "secret_key",
    "passphrase",
    "cookie",
    "authorization",
    "wallet",
    "account_id",
    "access_token",
    "private_key",
    "bearer ",
)

BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1: Dict[str, Any] = {
    "exchange": "bitget",
    "product_type": "USDT-FUTURES",
    "exchange_contract_id": "XAUUSDT_UMCBL",
    "symbol": "XAUUSDT",
    "price_precision": 2,
    "tick_size": "0.01",
    "qty_precision": 2,
    "qty_step": "0.01",
}

# Strictly public read-only endpoints matching existing repository services (zero private/order endpoints)
BITGET_PUBLIC_READONLY_ENDPOINTS_KH02_R2_C1: Dict[str, Dict[str, Any]] = {
    "history_candles": {
        "url": "https://api.bitget.com/api/v2/mix/market/history-candles",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "historical_l1_bbo_available": False,
        "reused_from_repo_module": "aurum-desk/backend/services/kh_01a_foundation_service.py",
    },
    "public_ticker": {
        "url": "https://api.bitget.com/api/v2/mix/market/ticker",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "historical_l1_bbo_available": False,
        "prospective_top_of_book_capable": True,
        "reused_from_repo_module": "aurum-desk/backend/services/market_shadow_service.py",
    },
    "orderbook_v3": {
        "url": "https://api.bitget.com/api/v3/market/orderbook",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "historical_l1_bbo_available": False,
        "prospective_top_of_book_capable": True,
        "reused_from_repo_module": "aurum-desk/backend/services/bitget_depth_service.py",
    },
    "contracts_metadata": {
        "url": "https://api.bitget.com/api/v2/mix/market/contracts",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "reused_from_repo_module": "aurum-desk/backend/services/instrument_provider.py",
    },
}

PROSPECTIVE_SNAPSHOT_C1_REQUIRED_EXTRA_FIELDS: Tuple[str, ...] = (
    "provider",
    "product_type",
    "tick_size",
    "qty_step",
    "endpoint",
    "request_utc_ms",
    "response_utc_ms",
    "query_params",
    "page_boundaries",
    "cursor",
    "http_status",
    "pagination_complete",
    "rate_limit_behavior",
    "license_or_access",
    "canonical_parsed_sha256",
    "event_or_close_utc_ms",
    "clock_skew_ms",
    "data_origin",
    "partition_role",
    "dev_oos_physically_separated",
)

PRIOR_ESCROW_READONLY_PATHS: Tuple[str, ...] = (
    "aurum-desk/docs/artifacts/kh_01b_r1/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_data_safety/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/latest_run.json",
    "aurum-desk/docs/artifacts/kh_01b_r1_ram_safety_r3/latest_run.json",
    "aurum-desk/docs/artifacts/kh_02_entry_foundation/latest_run.json",
    "aurum-desk/docs/artifacts/kh_02_r2_research_integrity/latest_run.json",
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

    # Reuse authoritative read-only KH-01B.R1 escrow registry validator (lazy import keeps startup <60ms)
    from services.kh_01b_r1_entry_edge_discovery_service import (
        build_and_validate_future_oos_escrow_registry,
    )
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


# ==============================================================================
# KH-02.R2.C1 STAGE B — AUTHORITATIVE FREEZE AUDIT, STRICT SNAPSHOT CONTRACT,
# HASH-LINKED APPEND-ONLY REGISTRY, POWER ANALYSIS & READ-ONLY COLLECTOR INTERFACE
# ==============================================================================


def scan_for_forbidden_secrets_r2_c1(obj: Any, *, _path: str = "$") -> List[str]:
    """
    Recursively scans snapshot metadata/logs for credentials, API keys, cookies,
    account IDs, or wallet fields (§5.4). Returns list of offending JSON paths.
    """
    hits: List[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            kl = str(k).strip().lower()
            child_path = f"{_path}.{k}"
            for tok in FORBIDDEN_SECRET_OR_ACCOUNT_SUBSTRINGS:
                if tok in kl:
                    hits.append(f"FORBIDDEN_KEY:{child_path}")
                    break
            hits.extend(scan_for_forbidden_secrets_r2_c1(v, _path=child_path))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            hits.extend(scan_for_forbidden_secrets_r2_c1(item, _path=f"{_path}[{i}]"))
    elif isinstance(obj, str):
        sl = obj.strip().lower()
        for tok in ("bearer ", "set-cookie:", "bg-access-key", "x-api-key", "api_secret="):
            if tok in sl:
                hits.append(f"FORBIDDEN_SECRET_VALUE:{_path}:{tok.strip()}")
                break
    return hits


def audit_authoritative_r2_freeze_manifest_r2_c1(repo_root: Path) -> Dict[str, Any]:
    """
    §5.2: Reads the authoritative KH-02.R2 artifact `14` directly from disk, checks its
    SHA256, and verifies that code constants and on-disk manifest match verbatim.
    If code and manifest differ, returns FAIL_FREEZE_MANIFEST_MISMATCH rather than
    silently rewriting freeze or embargo timestamps.
    """
    repo_root = Path(repo_root).resolve()
    p14 = repo_root / R2_AUTHORITATIVE_ARTIFACT_14_REL
    violations: List[str] = []
    actual_sha = _sha256_file(p14) if p14.is_file() else ""
    if not p14.is_file():
        violations.append(f"AUTHORITATIVE_R2_ARTIFACT_14_MISSING:{R2_AUTHORITATIVE_ARTIFACT_14_REL}")
        doc: Dict[str, Any] = {}
    else:
        if actual_sha != R2_AUTHORITATIVE_ARTIFACT_14_SHA256:
            violations.append(
                f"AUTHORITATIVE_R2_ARTIFACT_14_SHA_MISMATCH:expected={R2_AUTHORITATIVE_ARTIFACT_14_SHA256},actual={actual_sha}"
            )
        doc = json.loads(p14.read_text(encoding="utf-8"))

    expected_fields = {
        "frozen_candidate_code_sha": R2_AUTHORITATIVE_FROZEN_CODE_SHA,
        "frozen_config_sha256": R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
        "freeze_timestamp_ms": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        "freeze_timestamp_utc": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_UTC,
        "embargo_minutes": R2_AUTHORITATIVE_EMBARGO_MINUTES,
        "embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
        "embargo_cutoff_utc": R2_AUTHORITATIVE_EMBARGO_CUTOFF_UTC,
    }
    observed_fields: Dict[str, Any] = {}
    for k, exp_v in expected_fields.items():
        act_v = doc.get(k)
        observed_fields[k] = act_v
        if doc and act_v != exp_v:
            violations.append(f"FREEZE_MANIFEST_FIELD_MISMATCH:{k}:expected={exp_v},actual={act_v}")

    if doc and int(doc.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append(f"AUTHORITATIVE_R2_ARTIFACT_14_NONZERO_OOS_EVALUATIONS:{doc.get('OOS_EVALUATIONS')}")

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "authoritative_source_path": R2_AUTHORITATIVE_ARTIFACT_14_REL,
        "expected_sha256": R2_AUTHORITATIVE_ARTIFACT_14_SHA256,
        "actual_sha256": actual_sha,
        "sha256_match": actual_sha == R2_AUTHORITATIVE_ARTIFACT_14_SHA256,
        "expected_freeze_coordinates": expected_fields,
        "observed_freeze_coordinates": observed_fields,
        "embargo_boundary_semantics": EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
        "inclusive_boundary_example": {
            "accepted_earliest_start_utc_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
            "rejected_latest_blocked_start_utc_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS - 1,
        },
        "violations": violations,
        "status": "PASS" if not violations else "FAIL_FREEZE_MANIFEST_MISMATCH",
    }


def normalize_ny_local_to_utc_ms_r2_c1(local_iso_str: str, *, fold: int = 0) -> Dict[str, Any]:
    """
    Normalizes an America/New_York wall-clock timestamp (including DST spring-forward /
    fall-back transitions with explicit `fold` in {0, 1}) to strict UTC milliseconds.
    Rejects non-existent spring-forward gap times fail-closed.
    """
    if fold not in (0, 1):
        raise ValueError(f"INVALID_DST_FOLD:{fold}")
    ny_tz = ZoneInfo("America/New_York")
    naive = datetime.fromisoformat(local_iso_str)
    if naive.tzinfo is not None:
        dt_utc = naive.astimezone(timezone.utc)
        dt_ny = dt_utc.astimezone(ny_tz)
        return {
            "utc_ms": int(round(dt_utc.timestamp() * 1000)),
            "utc_iso": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ny_offset_hours": round((dt_ny.utcoffset().total_seconds() if dt_ny.utcoffset() else 0.0) / 3600.0, 2),
            "is_dst": bool(dt_ny.dst() and dt_ny.dst().total_seconds() != 0),
        }
    dt_ny = naive.replace(tzinfo=ny_tz, fold=fold)
    dt_utc = dt_ny.astimezone(timezone.utc)
    roundtrip = dt_utc.astimezone(ny_tz)
    if (
        roundtrip.year != naive.year
        or roundtrip.month != naive.month
        or roundtrip.day != naive.day
        or roundtrip.hour != naive.hour
        or roundtrip.minute != naive.minute
        or roundtrip.second != naive.second
    ):
        raise ValueError(f"NON_EXISTENT_DST_SPRING_FORWARD_LOCAL_TIME:{local_iso_str}")
    return {
        "utc_ms": int(round(dt_utc.timestamp() * 1000)),
        "utc_iso": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ny_offset_hours": round((dt_ny.utcoffset().total_seconds() if dt_ny.utcoffset() else 0.0) / 3600.0, 2),
        "is_dst": bool(dt_ny.dst() and dt_ny.dst().total_seconds() != 0),
        "fold": int(fold),
    }


def deduplicate_prospective_records_r2_c1(
    records: Sequence[Mapping[str, Any]],
    *,
    key_field: str = "ts_ms",
) -> Dict[str, Any]:
    """
    Crash/restart deduplication & idempotency helper: merges repeated captures deterministically.
    Raises ValueError if the same key carries conflicting payload contents (silent corruption).
    """
    by_key: Dict[Any, Tuple[str, Mapping[str, Any]]] = {}
    exact_duplicates_dropped = 0
    conflicts: List[str] = []
    for rec in records:
        if key_field not in rec:
            raise ValueError(f"MISSING_DEDUP_KEY_FIELD:{key_field}")
        k = rec[key_field]
        h = canonical_hash(dict(rec))
        if k not in by_key:
            by_key[k] = (h, dict(rec))
        else:
            prev_h, _ = by_key[k]
            if prev_h == h:
                exact_duplicates_dropped += 1
            else:
                conflicts.append(f"CONFLICTING_DUPLICATE_KEY:{k}")
    if conflicts:
        raise ValueError(";".join(conflicts))
    ordered = [by_key[k][1] for k in sorted(by_key.keys())]
    return {
        "input_count": len(records),
        "unique_count": len(ordered),
        "exact_duplicates_dropped": exact_duplicates_dropped,
        "canonical_sha256": canonical_hash(ordered),
        "records": ordered,
    }


def validate_prospective_snapshot_contract_c1(
    entry: Mapping[str, Any],
    *,
    freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    known_dev_shas: Sequence[str] = (
        CANONICAL_DATASET_SHA256_KH02,
        KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    ),
    embargo_ms: int = PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2,
    max_clock_skew_ms: int = 5_000,
    max_fee_tier_age_days: int = 30,
    now_utc_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    KH-02.R2.C1 (§5.4) strong typed append-only PROSPECTIVE SNAPSHOT validator.
    Enforces every contract invariant without ever running any strategy evaluation.
    """
    base_res = validate_prospective_snapshot_entry_r2(
        entry,
        freeze_timestamp_ms=freeze_timestamp_ms,
        known_dev_shas=known_dev_shas,
        embargo_ms=embargo_ms,
    )
    violations: List[str] = list(base_res["violations"])

    # 1. C1 required extra contract fields
    missing_c1 = [k for k in PROSPECTIVE_SNAPSHOT_C1_REQUIRED_EXTRA_FIELDS if k not in entry or entry.get(k) is None]
    if missing_c1:
        violations.append(f"MISSING_C1_CONTRACT_FIELDS:{','.join(missing_c1)}")

    # 2. Exact Bitget XAUUSDT contract family & precision
    if str(entry.get("exchange") or "").lower() != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange"]:
        violations.append(f"UNSUPPORTED_EXCHANGE:{entry.get('exchange')}")
    if str(entry.get("product_type") or "").upper() != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["product_type"]:
        violations.append(f"UNSUPPORTED_PRODUCT_TYPE:{entry.get('product_type')}")
    if str(entry.get("symbol") or "").upper() != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["symbol"]:
        violations.append(f"UNSUPPORTED_SYMBOL:{entry.get('symbol')}")
    if int(entry.get("price_precision") or -1) != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["price_precision"]:
        violations.append(f"PRICE_PRECISION_MISMATCH:{entry.get('price_precision')}")
    if int(entry.get("qty_precision") or -1) != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_precision"]:
        violations.append(f"QTY_PRECISION_MISMATCH:{entry.get('qty_precision')}")
    if str(entry.get("tick_size") or "") != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["tick_size"]:
        violations.append(f"TICK_SIZE_MISMATCH:{entry.get('tick_size')}")
    if str(entry.get("qty_step") or "") != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_step"]:
        violations.append(f"QTY_STEP_MISMATCH:{entry.get('qty_step')}")

    # 3. Strict provenance enum (OBSERVED_QUOTE / OBSERVED_TRADE / OHLCV_SNAPSHOT)
    origin = str(entry.get("data_origin") or "").upper()
    if origin not in VALID_PROSPECTIVE_DATA_ORIGINS:
        violations.append(f"INVALID_DATA_ORIGIN_ENUM:{origin}")
    quote_mode = str(entry.get("quote_mode") or "").upper()
    obs_bbo = bool(entry.get("observed_bid_ask_present", False))
    if origin == "OBSERVED_QUOTE" and (not obs_bbo or quote_mode in ("MODELED_BID_ASK", "SYNTHETIC_BID_ASK")):
        violations.append("MODELED_OR_SYNTHETIC_BBO_FORBIDDEN_AS_OBSERVED_QUOTE")
    if origin == "OHLCV_SNAPSHOT" and quote_mode == "OBSERVED_L1_BBO":
        violations.append("OHLCV_ONLY_CANNOT_CLAIM_OBSERVED_L1_BBO")
    if bool(entry.get("is_synthetic_fixture", False)):
        violations.append("SYNTHETIC_FIXTURE_FORBIDDEN_AS_REAL_OOS_MARKET_DATA")

    # 4. Canonical parsed checksum, HTTP status, pagination completeness, request/response causality
    parsed_sha = str(entry.get("canonical_parsed_sha256") or "").strip().lower()
    if len(parsed_sha) != 64:
        violations.append("INVALID_CANONICAL_PARSED_SHA256_LENGTH")
    http_status = int(entry.get("http_status") or 0)
    if http_status != 200:
        violations.append(f"NON_200_HTTP_STATUS:{http_status}")
    if not bool(entry.get("pagination_complete", False)):
        violations.append("PARTIAL_OR_INCOMPLETE_PAGINATION_REJECTED")
    req_ms = int(entry.get("request_utc_ms") or 0)
    resp_ms = int(entry.get("response_utc_ms") or 0)
    if req_ms <= 0 or resp_ms < req_ms:
        violations.append("INVALID_HTTP_REQUEST_RESPONSE_TIMESTAMPS")
    event_ms = int(entry.get("event_or_close_utc_ms") or 0)
    if event_ms <= 0 or int(entry.get("known_at_ms") or 0) < event_ms:
        violations.append("KNOWN_AT_BEFORE_EVENT_OR_CLOSE_UTC")

    # 5. Clock skew bounds
    skew_ms = int(entry.get("clock_skew_ms") or 0)
    if abs(skew_ms) > int(max_clock_skew_ms):
        violations.append(f"EXCESSIVE_CLOCK_SKEW_MS:{skew_ms}>max={max_clock_skew_ms}")

    # 6. Physical & logical DEV vs untouched OOS separation
    if str(entry.get("partition_role") or "").upper() != "FRESH_UNTOUCHED":
        violations.append(f"INVALID_PARTITION_ROLE_FOR_OOS:{entry.get('partition_role')}")
    if not bool(entry.get("dev_oos_physically_separated", False)):
        violations.append("DEV_OOS_PHYSICAL_SEPARATION_NOT_VERIFIED")
    storage_path = str(entry.get("storage_path") or "")
    if "historical_dataset_XAUUSDT.json.gz" in storage_path:
        violations.append("OOS_STORAGE_PATH_COLLIDES_WITH_DEV_DATASET")

    # 7. Stale account fee tier audit (never silently treat stale or unverified fee as live verified)
    fee_tier_status = str(entry.get("account_fee_tier_status") or "UNKNOWN_NOT_LOCALLY_VERIFIED").upper()
    if fee_tier_status == "VERIFIED_ACCOUNT_FEE_TIER":
        ver_ms = int(entry.get("account_fee_verified_utc_ms") or 0)
        ref_now = int(now_utc_ms if now_utc_ms is not None else int(entry.get("capture_time_ms") or 0))
        if ver_ms <= 0 or (ref_now - ver_ms) > int(max_fee_tier_age_days) * 86_400_000:
            violations.append("STALE_OR_UNVERIFIED_ACCOUNT_FEE_TIER")

    # 8. Secret / credential / cookie / wallet / account ID redaction check
    secret_hits = scan_for_forbidden_secrets_r2_c1(entry)
    if secret_hits:
        violations.append(f"FORBIDDEN_SECRETS_OR_ACCOUNT_FIELDS_PRESENT:{','.join(secret_hits[:5])}")

    # Deduplicate violations preserving order
    dedup_v: List[str] = []
    for v in violations:
        if v not in dedup_v:
            dedup_v.append(v)

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "snapshot_id": str(entry.get("snapshot_id") or ""),
        "valid_for_escrow_admission": len(dedup_v) == 0,
        "violations": dedup_v,
        "embargo_cutoff_ms": int(freeze_timestamp_ms) + int(embargo_ms),
        "embargo_boundary_semantics": EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
        "data_origin": origin,
        "observed_bid_ask_present": obs_bbo,
        "dataset_sha256": str(entry.get("dataset_sha256") or "").lower(),
        "canonical_parsed_sha256": parsed_sha,
    }


def validate_prospective_payload_bars_and_quotes_r2_c1(
    raw_bytes: bytes,
    *,
    is_gzipped: bool = True,
    expected_raw_sha256: Optional[str] = None,
    timeframe_ms: int = 60_000,
    max_spread_outlier_usd: float = 2.50,
    stale_quote_threshold_ms: int = 15_000,
    disconnect_gap_threshold_ms: int = 300_000,
    long_closure_threshold_ms: int = 48 * 3_600_000,
    is_synthetic_fixture: bool = False,
) -> Dict[str, Any]:
    """
    Validates raw gzip/JSON market-data payloads (OHLCV bars, observed L1 quotes, and/or
    observed trades) for integrity, closed-bar invariants, monotonic UTC, sequence continuity,
    crossed BBO, spread outliers, stale quotes, disconnect gaps, and long market closures.
    """
    violations: List[str] = []
    warnings: List[str] = []
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if expected_raw_sha256 and raw_sha != expected_raw_sha256.lower():
        violations.append(f"RAW_RESPONSE_SHA256_MISMATCH:expected={expected_raw_sha256},actual={raw_sha}")

    try:
        decoded = gzip.decompress(raw_bytes) if is_gzipped else raw_bytes
    except Exception as exc:
        return {
            "valid": False,
            "is_synthetic_fixture": bool(is_synthetic_fixture),
            "fixture_provenance_label": (
                "SYNTHETIC_VALIDATOR_ONLY_NOT_MARKET_DATA" if is_synthetic_fixture else "RAW_EXTERNAL_PAYLOAD"
            ),
            "raw_sha256": raw_sha,
            "canonical_parsed_sha256": None,
            "violations": violations + [f"CORRUPT_GZIP_PAYLOAD:{type(exc).__name__}"],
            "warnings": [],
        }

    try:
        doc = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        return {
            "valid": False,
            "is_synthetic_fixture": bool(is_synthetic_fixture),
            "fixture_provenance_label": (
                "SYNTHETIC_VALIDATOR_ONLY_NOT_MARKET_DATA" if is_synthetic_fixture else "RAW_EXTERNAL_PAYLOAD"
            ),
            "raw_sha256": raw_sha,
            "canonical_parsed_sha256": None,
            "violations": violations + [f"MALFORMED_JSON_PAYLOAD:{type(exc).__name__}"],
            "warnings": [],
        }

    bars = list(doc.get("bars") or ())
    quotes = list(doc.get("quotes") or ())
    trades = list(doc.get("trades") or ())
    if not bars and not quotes and not trades:
        violations.append("EMPTY_MARKET_DATA_PAYLOAD")

    # Audit candles (closed bars only, strict monotonic UTC, OHLC invariants, gaps & long closures)
    prev_close_ms: Optional[int] = None
    bar_gap_count = 0
    long_closure_count = 0
    for i, b in enumerate(bars):
        if not bool(b.get("is_closed", False)):
            violations.append(f"UNCLOSED_CANDLE_FORBIDDEN:idx={i}")
        o_ms = int(b.get("open_time_ms") or b.get("open_time") or 0)
        c_ms = int(b.get("close_time_ms") or b.get("close_time") or 0)
        if o_ms <= 0 or c_ms <= o_ms:
            violations.append(f"INVALID_BAR_TIMESTAMPS:idx={i}")
        if prev_close_ms is not None:
            if c_ms <= prev_close_ms:
                violations.append(f"OUT_OF_ORDER_OR_DUPLICATE_BAR_UTC:idx={i}:{c_ms}<={prev_close_ms}")
            delta = c_ms - prev_close_ms
            if delta > long_closure_threshold_ms:
                long_closure_count += 1
                warnings.append(f"LONG_MARKET_CLOSURE_DETECTED:idx={i}:gap_ms={delta}")
            elif delta > timeframe_ms:
                bar_gap_count += 1
        prev_close_ms = c_ms
        o_px, h_px, l_px, c_px = float(b.get("open", 0)), float(b.get("high", 0)), float(b.get("low", 0)), float(b.get("close", 0))
        if min(o_px, h_px, l_px, c_px) <= 0 or h_px < max(o_px, c_px) or l_px > min(o_px, c_px) or l_px > h_px:
            violations.append(f"CORRUPTED_OHLC_INVARIANT:idx={i}")

    # Audit quotes (strict sequence continuity, monotonic receive/event UTC, crossed BBO, outliers, stale, disconnect)
    prev_q_ts: Optional[int] = None
    prev_q_seq: Optional[int] = None
    spreads: List[float] = []
    outlier_spreads = 0
    stale_quotes = 0
    disconnect_gaps = 0
    for i, q in enumerate(quotes):
        ev_ms = int(q.get("event_utc_ms") or 0)
        rx_ms = int(q.get("receive_utc_ms") or ev_ms)
        if ev_ms <= 0 or rx_ms < ev_ms:
            violations.append(f"INVALID_QUOTE_EVENT_OR_RECEIVE_UTC:idx={i}")
        if prev_q_ts is not None:
            if ev_ms < prev_q_ts:
                violations.append(f"OUT_OF_ORDER_QUOTE_PACKET:idx={i}:{ev_ms}<{prev_q_ts}")
            elif (ev_ms - prev_q_ts) > disconnect_gap_threshold_ms:
                disconnect_gaps += 1
                warnings.append(f"QUOTE_DISCONNECT_GAP:idx={i}:gap_ms={ev_ms - prev_q_ts}")
        prev_q_ts = ev_ms
        if "seq" not in q or q.get("seq") is None:
            violations.append(f"MISSING_QUOTE_SEQUENCE_NUMBER:idx={i}")
        else:
            seq = int(q["seq"])
            if prev_q_seq is not None and seq != prev_q_seq + 1:
                violations.append(f"MISSING_OR_BROKEN_QUOTE_SEQUENCE:idx={i}:prev={prev_q_seq},curr={seq}")
            prev_q_seq = seq
        bid = float(q.get("bid", 0.0))
        ask = float(q.get("ask", 0.0))
        if bid <= 0.0 or ask <= 0.0 or bid >= ask:
            violations.append(f"CROSSED_OR_INVALID_BBO:idx={i}:bid={bid},ask={ask}")
        else:
            sp = round(ask - bid, 6)
            spreads.append(sp)
            if sp > max_spread_outlier_usd:
                outlier_spreads += 1
                warnings.append(f"SPREAD_OUTLIER:idx={i}:spread={sp}")
        if (rx_ms - ev_ms) > stale_quote_threshold_ms:
            stale_quotes += 1
            warnings.append(f"STALE_QUOTE:idx={i}:lag_ms={rx_ms - ev_ms}")

    # Audit trades (sequence continuity / missing trades / monotonic UTC)
    prev_t_ts: Optional[int] = None
    prev_t_seq: Optional[int] = None
    for i, tr in enumerate(trades):
        t_ms = int(tr.get("event_utc_ms") or 0)
        if t_ms <= 0:
            violations.append(f"INVALID_TRADE_UTC:idx={i}")
        if prev_t_ts is not None and t_ms < prev_t_ts:
            violations.append(f"OUT_OF_ORDER_TRADE_PACKET:idx={i}:{t_ms}<{prev_t_ts}")
        prev_t_ts = t_ms
        if "trade_seq" not in tr or tr.get("trade_seq") is None:
            violations.append(f"MISSING_TRADE_SEQUENCE_ID:idx={i}")
        else:
            tseq = int(tr["trade_seq"])
            if prev_t_seq is not None and tseq != prev_t_seq + 1:
                violations.append(f"MISSING_TRADES_GAP:idx={i}:prev={prev_t_seq},curr={tseq}")
            prev_t_seq = tseq

    parsed_sha = canonical_hash(doc)
    return {
        "valid": len(violations) == 0,
        "is_synthetic_fixture": bool(is_synthetic_fixture),
        "fixture_provenance_label": (
            "SYNTHETIC_VALIDATOR_ONLY_NOT_MARKET_DATA" if is_synthetic_fixture else "RAW_EXTERNAL_PAYLOAD"
        ),
        "raw_sha256": raw_sha,
        "canonical_parsed_sha256": parsed_sha,
        "bars_count": len(bars),
        "quotes_count": len(quotes),
        "trades_count": len(trades),
        "bar_gap_count": bar_gap_count,
        "long_closure_count": long_closure_count,
        "measured_spread_summary": {
            "count": len(spreads),
            "min_usd": min(spreads) if spreads else None,
            "median_usd": sorted(spreads)[len(spreads) // 2] if spreads else None,
            "max_usd": max(spreads) if spreads else None,
            "outlier_count": outlier_spreads,
            "stale_quote_count": stale_quotes,
            "disconnect_gap_count": disconnect_gaps,
        },
        "violations": violations,
        "warnings": warnings,
    }


class ProspectiveHashLinkedEscrowRegistryR2C1:
    """
    §5.4: Immutable, hash-linked, write-once append-only registry for prospective OOS
    snapshots. Prevents overwriting existing snapshots, enforces idempotency on exact
    repeated ingestion, and detects any post-hoc tampering via SHA256 hash chaining.
    """

    GENESIS_PREV_SHA256 = "0" * 64

    def __init__(self, registry_path: Path) -> None:
        self.registry_path = Path(registry_path).resolve()

    def _read_records(self) -> List[Dict[str, Any]]:
        if not self.registry_path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        for idx, line in enumerate(self.registry_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception as exc:
                raise ValueError(f"TAMPERED_OR_MALFORMED_REGISTRY_LINE:{idx}:{exc}") from exc
        return out

    def verify_chain_integrity(self) -> Dict[str, Any]:
        violations: List[str] = []
        try:
            records = self._read_records()
        except Exception as exc:
            return {"valid": False, "record_count": 0, "violations": [str(exc)]}

        prev = self.GENESIS_PREV_SHA256
        seen_ids: set[str] = set()
        for idx, rec in enumerate(records):
            sid = str(rec.get("snapshot_id") or "")
            if sid in seen_ids:
                violations.append(f"DUPLICATE_SNAPSHOT_ID_IN_REGISTRY:{sid}")
            seen_ids.add(sid)
            if rec.get("prev_entry_sha256") != prev:
                violations.append(f"BROKEN_HASH_CHAIN_PREV_MISMATCH:idx={idx}")
            body = {k: v for k, v in rec.items() if k != "entry_sha256"}
            recomp = canonical_hash(body)
            if rec.get("entry_sha256") != recomp:
                violations.append(f"TAMPERED_REGISTRY_ENTRY_SHA256:idx={idx}:sid={sid}")
            prev = str(rec.get("entry_sha256") or "")

        return {
            "valid": len(violations) == 0,
            "record_count": len(records),
            "head_entry_sha256": prev,
            "violations": violations,
        }

    def append_validated_snapshot(
        self,
        entry: Mapping[str, Any],
        *,
        freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
    ) -> Dict[str, Any]:
        """
        KH-02.R2.C2 hardened compatibility entrypoint: acquires inter-process fcntl.flock(LOCK_EX)
        and appends strictly via os.open(..., O_WRONLY|O_CREAT|O_APPEND) + fsync + dir fsync.
        NEVER reserializes or rewrites prior journal lines on success.
        """
        val = validate_prospective_snapshot_contract_c1(
            entry,
            freeze_timestamp_ms=freeze_timestamp_ms,
            known_dev_shas=known_dev_shas,
        )
        if not val["valid_for_escrow_admission"]:
            raise ValueError(f"INVALID_PROSPECTIVE_SNAPSHOT:{val['violations']}")

        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        lock_p = self.registry_path.with_name(f".{self.registry_path.name}.lock")
        lfd = os.open(os.fspath(lock_p), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lfd, fcntl.LOCK_EX)
            chain_check = self.verify_chain_integrity()
            if not chain_check["valid"]:
                raise ValueError(f"TAMPERED_REGISTRY_CANNOT_APPEND:{chain_check['violations']}")

            records = self._read_records()
            sid = str(entry["snapshot_id"])
            content_sha = canonical_hash(dict(entry))
            for existing in records:
                if existing.get("snapshot_id") == sid:
                    if existing.get("snapshot_content_sha256") == content_sha:
                        return {
                            "action": "IDEMPOTENT_ALREADY_RECORDED_NOOP",
                            "snapshot_id": sid,
                            "entry_sha256": existing["entry_sha256"],
                            "record_count": len(records),
                        }
                    raise PermissionError(f"IMMUTABLE_REGISTRY_OVERWRITE_FORBIDDEN:{sid}")

            prev_sha = records[-1]["entry_sha256"] if records else self.GENESIS_PREV_SHA256
            new_body = {
                "seq_index": len(records),
                "snapshot_id": sid,
                "prev_entry_sha256": prev_sha,
                "snapshot_content_sha256": content_sha,
                "dataset_sha256": val["dataset_sha256"],
                "raw_response_sha256": str(entry["raw_response_sha256"]).lower(),
                "canonical_parsed_sha256": val["canonical_parsed_sha256"],
                "data_origin": val["data_origin"],
                "start_utc_ms": int(entry["start_utc_ms"]),
                "end_utc_ms": int(entry["end_utc_ms"]),
                "OOS_EVALUATIONS": 0,
            }
            new_rec = dict(new_body, entry_sha256=canonical_hash(new_body))
            line_b = (json.dumps(new_rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            jfd = os.open(os.fspath(self.registry_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                written = os.write(jfd, line_b)
                if written != len(line_b):
                    raise IOError(f"SHORT_APPEND_WRITE:{written}!={len(line_b)}")
                os.fsync(jfd)
            finally:
                os.close(jfd)
            dfd = os.open(os.fspath(self.registry_path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
            return {
                "action": "APPENDED_NEW_ENTRY",
                "snapshot_id": sid,
                "entry_sha256": new_rec["entry_sha256"],
                "record_count": len(records) + 1,
                "append_only_prior_bytes_untouched": True,
            }
        finally:
            try:
                fcntl.flock(lfd, fcntl.LOCK_UN)
            finally:
                os.close(lfd)


def compute_prospective_oos_power_requirements_r2_c1(
    *,
    num_primary_arms: int = 4,
    alpha_familywise: float = 0.05,
    target_power: float = 0.80,
    std_r_per_trade: float = 1.25,
    ny_day_cluster_vif: float = 1.50,
    effect_sizes_r: Sequence[float] = (0.15, 0.25, 0.35, 0.50),
) -> Dict[str, Any]:
    """
    §5.5: Quantifies realistic prospective untouched-OOS sample-size requirements under
    Holm-Bonferroni family-wise error control (m primary arms) and NY-day clustering VIF,
    explicitly demonstrating why a fixed N = 30 closed trades CANNOT prove economic edge.
    """
    # Conservative Bonferroni-adjusted two-sided normal quantile (exact z via erfcinv approx)
    # Using standard exact z-values for common alpha/m levels without external scipy dependency:
    alpha_adj = float(alpha_familywise) / max(1, int(num_primary_arms))
    # Approximate inverse standard normal tail via binary search on math.erfc
    def _z_two_sided(a: float) -> float:
        lo, hi = 0.0, 8.0
        for _ in range(64):
            mid = 0.5 * (lo + hi)
            tail = math.erfc(mid / math.sqrt(2.0))
            if tail > a:
                lo = mid
            else:
                hi = mid
        return round(0.5 * (lo + hi), 4)

    z_alpha = _z_two_sided(alpha_adj)
    z_beta = _z_two_sided(2.0 * (1.0 - float(target_power)))
    rows: List[Dict[str, Any]] = []
    for mu_r in effect_sizes_r:
        n_iid = math.ceil(((z_alpha + z_beta) * float(std_r_per_trade) / float(mu_r)) ** 2)
        n_clustered = math.ceil(n_iid * float(ny_day_cluster_vif))
        # Power of fixed N = 30 clustered trades (effective N = 30 / VIF)
        n_eff_30 = 30.0 / float(ny_day_cluster_vif)
        ncp_30 = (float(mu_r) / float(std_r_per_trade)) * math.sqrt(n_eff_30)
        # Approximate one-sided detection probability beyond z_alpha
        power_n30 = round(0.5 * math.erfc((z_alpha - ncp_30) / math.sqrt(2.0)), 4)
        rows.append(
            {
                "target_net_expectancy_r": float(mu_r),
                "required_closed_trades_iid": int(n_iid),
                "required_closed_trades_clustered_vif": int(n_clustered),
                "power_at_fixed_n_30_clustered": power_n30,
                "fixed_n_30_sufficient": bool(power_n30 >= float(target_power)),
            }
        )

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "num_primary_arms_holm_family": int(num_primary_arms),
        "familywise_alpha_two_sided": float(alpha_familywise),
        "bonferroni_strict_alpha_per_arm": round(alpha_adj, 6),
        "z_alpha_two_sided": z_alpha,
        "z_beta_power_80pct": z_beta,
        "assumed_trade_r_std": float(std_r_per_trade),
        "ny_day_clustering_vif": float(ny_day_cluster_vif),
        "sample_size_grid": rows,
        "fixed_30_trades_proves_edge": False,
        "statistical_disclosure": (
            "A fixed N = 30 closed trades is severely UNDERPOWERED under clustered gold-futures returns "
            "and multi-arm Holm-Bonferroni correction. Prospective OOS must span multiple distinct "
            "volatility/trend regimes, record observed L1 BBO quotes, verify actual account fees, "
            "and meet pre-registered cluster-adjusted sample-size thresholds before any unsealing."
        ),
    }


class ReadOnlyBitgetPublicProspectiveCollectorR2C1:
    """
    §5.3: Strictly READ-ONLY public Bitget XAUUSDT prospective market-data acquisition
    interface. Network capture and persistent recording are DISABLED BY DEFAULT.
    Zero order endpoints, zero private/account/wallet endpoints, zero API-key reads.
    """

    def __init__(
        self,
        *,
        network_capture_enabled: bool = False,
        explicit_operator_approval_id: Optional[str] = None,
    ) -> None:
        self.network_capture_enabled = bool(network_capture_enabled)
        self.explicit_operator_approval_id = explicit_operator_approval_id
        self.allowed_endpoints = dict(BITGET_PUBLIC_READONLY_ENDPOINTS_KH02_R2_C1)

    def describe_readiness(self) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "read_only": True,
            "order_or_private_endpoints_present": False,
            "api_key_or_secret_reads_present": False,
            "network_capture_enabled": self.network_capture_enabled,
            "persistent_capture_active": False,
            "explicit_operator_approval_present": bool(self.explicit_operator_approval_id),
            "historical_l1_bbo_available_from_rest": False,
            "prospective_top_of_book_caveat": (
                "Public REST ticker/orderbook endpoints cannot backfill historical L1 BBO. "
                "When explicitly authorized in the future with >= 20.5 GiB strict disk free, "
                "prospective capture can only record live future top-of-book with explicit "
                "event_utc_ms, receive_utc_ms, sampling interval, and disconnect-gap logs."
            ),
            "allowed_public_endpoints": self.allowed_endpoints,
        }

    def fetch_public_snapshot(
        self,
        endpoint_key: str,
        *,
        write_mount_probe: Mapping[str, Any],
        transport: Optional[Any] = None,
        snapshot_id: str = "prosp_snap_001",
    ) -> Dict[str, Any]:
        """
        Fail-closed guard: refuses network capture unless separately authorized AND strict
        disk >= 20.5 GiB. When an injectable HTTP transport is supplied under explicit approval,
        delegates to C2PublicReadOnlyBitgetCollector (never launches unapproved live HTTP).
        """
        if endpoint_key not in self.allowed_endpoints:
            raise ValueError(f"FORBIDDEN_OR_UNKNOWN_ENDPOINT:{endpoint_key}")
        if not self.network_capture_enabled or not self.explicit_operator_approval_id:
            raise PermissionError(
                "PROSPECTIVE_NETWORK_CAPTURE_DISABLED_BY_DEFAULT: "
                "Separate explicit operator approval is required before launching live read-only capture."
            )
        if not bool(write_mount_probe.get("can_run_heavy_stages", False)):
            raise RuntimeError(
                f"BLOCKED_LOW_DISK_CAPTURE_FORBIDDEN:status={write_mount_probe.get('status')},min_free_gib={write_mount_probe.get('min_free_gib')}"
            )
        if transport is not None:
            from services.kh_02_r2_c2_public_capture import (
                C2PublicReadOnlyBitgetCollector,
                OperatorCaptureAuthorizationC2,
            )
            col = C2PublicReadOnlyBitgetCollector(
                authorization=OperatorCaptureAuthorizationC2(
                    approval_id=str(self.explicit_operator_approval_id),
                    approved_by="DELEGATED_COMPAT_CALLER",
                    network_capture_enabled=True,
                ),
                transport=transport,
                is_fake_transport_qa=True,
            )
            if endpoint_key in ("public_ticker", "orderbook_v3"):
                return col.fetch_public_l1_bbo_snapshot(
                    endpoint_key=endpoint_key,
                    snapshot_id=snapshot_id,
                    write_mount_probe=write_mount_probe,
                )
        raise PermissionError(
            "NO_HTTP_TRANSPORT_CONFIGURED_LIVE_NETWORK_DISABLED: "
            "Supply an injected fake transport for QA or commission a separate operator-approved capture session."
        )


def make_synthetic_valid_c1_snapshot_fixture(
    *,
    freeze_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    snapshot_id: str = "synth_fixture_prosp_001",
    start_offset_ms: int = PROSPECTIVE_OOS_EMBARGO_MS_KH02_R2,
) -> Dict[str, Any]:
    """
    Constructs a cleanly labeled SYNTHETIC snapshot metadata dict for validator unit testing.
    NOTE: Calling code testing real admission passes `is_synthetic_fixture=False` ONLY inside
    isolated validator unit tests, NEVER for real OOS evidence!
    """
    start_ms = int(freeze_ms) + int(start_offset_ms)
    end_ms = start_ms + 3_600_000
    return {
        "snapshot_id": snapshot_id,
        "schema_version": SCHEMA_VERSION_KH02_R2_C1,
        "provider": "bitget_public_rest",
        "exchange": "bitget",
        "product_type": "USDT-FUTURES",
        "exchange_contract_id": "XAUUSDT_UMCBL",
        "symbol": "XAUUSDT",
        "price_precision": 2,
        "tick_size": "0.01",
        "qty_precision": 2,
        "qty_step": "0.01",
        "timeframe": "1m",
        "endpoint": BITGET_PUBLIC_READONLY_ENDPOINTS_KH02_R2_C1["public_ticker"]["url"],
        "request_utc_ms": end_ms + 500,
        "response_utc_ms": end_ms + 650,
        "query_params": {"symbol": "XAUUSDT", "productType": "USDT-FUTURES"},
        "page_boundaries": [{"start_ms": start_ms, "end_ms": end_ms}],
        "cursor": "DONE",
        "http_status": 200,
        "pagination_complete": True,
        "rate_limit_behavior": "PUBLIC_UNAUTH_BACKOFF_OK",
        "license_or_access": "PUBLIC_MARKET_DATA_READONLY",
        "start_utc_ms": start_ms,
        "end_utc_ms": end_ms,
        "event_or_close_utc_ms": end_ms,
        "capture_time_ms": end_ms + 650,
        "known_at_ms": end_ms + 650,
        "clock_skew_ms": 12,
        "bar_count": 60,
        "utc_monotonic_strict": True,
        "data_origin": "OBSERVED_QUOTE",
        "observed_bid_ask_present": True,
        "quote_mode": "OBSERVED_L1_BBO",
        "raw_response_sha256": "a" * 64,
        "canonical_parsed_sha256": "c" * 64,
        "pagination_lineage": ["page_1_of_1"],
        "source_signature_or_checksum": "sha256_public_ok",
        "dataset_sha256": "d" * 64,
        "partition_role": "FRESH_UNTOUCHED",
        "dev_oos_physically_separated": True,
        "storage_path": "escrow_oos_isolated/synth_001.jsonl.gz",
        "account_fee_tier_status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "strategy_evaluation_count": 0,
    }


def run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(tmp_root: Path) -> Dict[str, Any]:
    """
    §5.6: Runs ALL 20 mandatory synthetic/mock OOS ingestion validator regression tests.
    EXPLICITLY LABELED `SYNTHETIC_VALIDATOR_ONLY_NOT_MARKET_DATA`: passing all 20 proves
    the validator rejects malformed/cheating inputs; it does NOT constitute real market data.
    """
    tmp_root = Path(tmp_root).resolve()
    tmp_root.mkdir(parents=True, exist_ok=True)
    freeze_ms = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS
    cutoff_ms = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS
    base_good = make_synthetic_valid_c1_snapshot_fixture(freeze_ms=freeze_ms)
    good_val = validate_prospective_snapshot_contract_c1(base_good, freeze_timestamp_ms=freeze_ms)
    assert good_val["valid_for_escrow_admission"], f"Base synthetic fixture must be valid: {good_val}"

    def _gz(obj: Mapping[str, Any]) -> bytes:
        return gzip.compress(json.dumps(obj, sort_keys=True).encode("utf-8"))

    good_bars = [
        {"open_time_ms": cutoff_ms, "close_time_ms": cutoff_ms + 60_000, "open": 4000.0, "high": 4001.0, "low": 3999.0, "close": 4000.5, "is_closed": True},
        {"open_time_ms": cutoff_ms + 60_000, "close_time_ms": cutoff_ms + 120_000, "open": 4000.5, "high": 4002.0, "low": 4000.0, "close": 4001.5, "is_closed": True},
    ]
    good_quotes = [
        {"seq": 1, "event_utc_ms": cutoff_ms + 1000, "receive_utc_ms": cutoff_ms + 1050, "bid": 4000.10, "ask": 4000.30},
        {"seq": 2, "event_utc_ms": cutoff_ms + 2000, "receive_utc_ms": cutoff_ms + 2040, "bid": 4000.15, "ask": 4000.35},
    ]
    good_trades = [
        {"trade_seq": 10, "event_utc_ms": cutoff_ms + 1000, "price": 4000.20, "qty": 0.1},
        {"trade_seq": 11, "event_utc_ms": cutoff_ms + 2000, "price": 4000.25, "qty": 0.2},
    ]
    good_payload_res = validate_prospective_payload_bars_and_quotes_r2_c1(
        _gz({"bars": good_bars, "quotes": good_quotes, "trades": good_trades}),
        is_synthetic_fixture=True,
    )
    assert good_payload_res["valid"], good_payload_res

    cases: List[Dict[str, Any]] = []

    def _add(cid: str, scenario: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        cases.append(
            {
                "case_id": cid,
                "scenario": scenario,
                "fixture_provenance": "SYNTHETIC_VALIDATOR_ONLY_NOT_MARKET_DATA",
                "red_rejected": bool(red_ok),
                "green_accepted": bool(green_ok),
                "status": "PASS" if (red_ok and green_ok) else "FAIL",
                "detail": detail,
            }
        )

    # 1. Post-freeze timestamp & exact embargo boundary (cutoff_ms - 1 REJECTED, cutoff_ms ACCEPTED)
    s01_bad = dict(base_good, start_utc_ms=cutoff_ms - 1)
    r01_bad = validate_prospective_snapshot_contract_c1(s01_bad, freeze_timestamp_ms=freeze_ms)
    r01_ok = validate_prospective_snapshot_contract_c1(dict(base_good, start_utc_ms=cutoff_ms), freeze_timestamp_ms=freeze_ms)
    _add("OOS-V01", "POST_FREEZE_AND_EXACT_EMBARGO_BOUNDARY", not r01_bad["valid_for_escrow_admission"], r01_ok["valid_for_escrow_admission"], f"cutoff-1 rejected={r01_bad['violations']}")

    # 2. Overlapping old DEV dataset SHA rejected
    s02_bad = dict(base_good, dataset_sha256=CANONICAL_DATASET_SHA256_KH02)
    r02_bad = validate_prospective_snapshot_contract_c1(s02_bad, freeze_timestamp_ms=freeze_ms)
    _add("OOS-V02", "OVERLAPPING_OLD_DEV_SHA_REJECTED", not r02_bad["valid_for_escrow_admission"], good_val["valid_for_escrow_admission"], ";".join(r02_bad["violations"]))

    # 3. Fake OBSERVED quote (modeled spread mislabeled as OBSERVED_QUOTE / OBSERVED_L1_BBO)
    s03_bad = dict(base_good, data_origin="OBSERVED_QUOTE", observed_bid_ask_present=False, quote_mode="MODELED_BID_ASK")
    r03_bad = validate_prospective_snapshot_contract_c1(s03_bad, freeze_timestamp_ms=freeze_ms)
    _add("OOS-V03", "FAKE_OBSERVED_QUOTE_REJECTED", not r03_bad["valid_for_escrow_admission"], good_val["valid_for_escrow_admission"], ";".join(r03_bad["violations"]))

    # 4. Open/unclosed candle rejected
    b04 = [dict(good_bars[0], is_closed=False)]
    r04_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"bars": b04}), is_synthetic_fixture=True)
    _add("OOS-V04", "OPEN_UNCLOSED_CANDLE_REJECTED", not r04_bad["valid"], good_payload_res["valid"], ";".join(r04_bad["violations"]))

    # 5. Missing trades sequence gap rejected
    t05 = [good_trades[0], dict(good_trades[1], trade_seq=15)]
    r05_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"trades": t05}), is_synthetic_fixture=True)
    _add("OOS-V05", "MISSING_TRADES_SEQUENCE_GAP_REJECTED", not r05_bad["valid"], good_payload_res["valid"], ";".join(r05_bad["violations"]))

    # 6. Missing quote sequence rejected
    q06 = [good_quotes[0], dict(good_quotes[1], seq=5)]
    r06_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"quotes": q06}), is_synthetic_fixture=True)
    _add("OOS-V06", "MISSING_QUOTE_SEQUENCE_REJECTED", not r06_bad["valid"], good_payload_res["valid"], ";".join(r06_bad["violations"]))

    # 7. DST / New York local time spring-forward nonexistent time rejected & fall-back fold handled
    dst_bad = False
    try:
        normalize_ny_local_to_utc_ms_r2_c1("2026-03-08T02:30:00")  # Non-existent NY spring-forward hour
    except ValueError:
        dst_bad = True
    edt_ok = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=0)
    est_ok = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=1)
    dst_green = (est_ok["utc_ms"] - edt_ok["utc_ms"] == 3_600_000) and (edt_ok["ny_offset_hours"] == -4.0) and (est_ok["ny_offset_hours"] == -5.0)
    _add("OOS-V07", "DST_NEW_YORK_LOCAL_TIME_HANDLING", dst_bad, dst_green, f"edt={edt_ok['utc_iso']}, est={est_ok['utc_iso']}")

    # 8. UTC normalization from tz-aware ISO offset matches exact UTC ms
    u08_a = normalize_ny_local_to_utc_ms_r2_c1("2026-11-21T14:43:52-05:00")
    u08_b = normalize_ny_local_to_utc_ms_r2_c1("2026-11-21T19:43:52+00:00")
    u08_ok = u08_a["utc_ms"] == u08_b["utc_ms"] and u08_a["utc_iso"] == "2026-11-21T19:43:52Z"
    s08_bad = dict(base_good, utc_monotonic_strict=False)
    r08_bad = validate_prospective_snapshot_contract_c1(s08_bad, freeze_timestamp_ms=freeze_ms)
    _add("OOS-V08", "UTC_NORMALIZATION_AND_STRICT_MONOTONIC", not r08_bad["valid_for_escrow_admission"], u08_ok, f"utc={u08_a['utc_iso']}")

    # 9. Out-of-order packets rejected
    q09 = [
        {"seq": 1, "event_utc_ms": cutoff_ms + 5000, "receive_utc_ms": cutoff_ms + 5010, "bid": 4000.1, "ask": 4000.3},
        {"seq": 2, "event_utc_ms": cutoff_ms + 4000, "receive_utc_ms": cutoff_ms + 5020, "bid": 4000.1, "ask": 4000.3},
    ]
    r09_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"quotes": q09}), is_synthetic_fixture=True)
    _add("OOS-V09", "OUT_OF_ORDER_PACKETS_REJECTED", not r09_bad["valid"], good_payload_res["valid"], ";".join(r09_bad["violations"]))

    # 10. Long market closure (> 48h weekend/holiday) flagged explicitly without silent masking
    b10 = [
        good_bars[0],
        dict(good_bars[1], open_time_ms=cutoff_ms + 52 * 3_600_000, close_time_ms=cutoff_ms + 52 * 3_600_000 + 60_000),
    ]
    r10 = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"bars": b10}), is_synthetic_fixture=True)
    _add("OOS-V10", "LONG_MARKET_CLOSURE_DISCLOSED", r10["long_closure_count"] == 1 and any("LONG_MARKET_CLOSURE" in w for w in r10["warnings"]), good_payload_res["long_closure_count"] == 0, f"closures={r10['long_closure_count']}")

    # 11. Crossed BBO (bid >= ask) rejected
    q11 = [dict(good_quotes[0], bid=4000.50, ask=4000.40)]
    r11_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"quotes": q11}), is_synthetic_fixture=True)
    _add("OOS-V11", "CROSSED_BBO_REJECTED", not r11_bad["valid"], good_payload_res["valid"], ";".join(r11_bad["violations"]))

    # 12. Partial HTTP page rejected
    s12_bad = dict(base_good, pagination_complete=False)
    r12_bad = validate_prospective_snapshot_contract_c1(s12_bad, freeze_timestamp_ms=freeze_ms)
    _add("OOS-V12", "PARTIAL_PAGE_REJECTED", not r12_bad["valid_for_escrow_admission"], good_val["valid_for_escrow_admission"], ";".join(r12_bad["violations"]))

    # 13. Reconnect / disconnect gap detected explicitly
    q13 = [
        good_quotes[0],
        dict(good_quotes[1], event_utc_ms=cutoff_ms + 500_000, receive_utc_ms=cutoff_ms + 500_050),
    ]
    r13 = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"quotes": q13}), is_synthetic_fixture=True)
    _add("OOS-V13", "RECONNECT_DISCONNECT_GAP_DETECTED", r13["measured_spread_summary"]["disconnect_gap_count"] == 1, good_payload_res["measured_spread_summary"]["disconnect_gap_count"] == 0, ";".join(r13["warnings"]))

    # 14. Crash/restart deduplication drops exact duplicates & rejects conflicting duplicate payloads
    dedup_ok = deduplicate_prospective_records_r2_c1([
        {"ts_ms": 1, "px": 4000.0},
        {"ts_ms": 2, "px": 4001.0},
        {"ts_ms": 1, "px": 4000.0},
    ])
    conflict_caught = False
    try:
        deduplicate_prospective_records_r2_c1([
            {"ts_ms": 1, "px": 4000.0},
            {"ts_ms": 1, "px": 4009.0},
        ])
    except ValueError:
        conflict_caught = True
    _add("OOS-V14", "CRASH_RESTART_DEDUPLICATION", conflict_caught, dedup_ok["unique_count"] == 2 and dedup_ok["exact_duplicates_dropped"] == 1, f"dropped={dedup_ok['exact_duplicates_dropped']}")

    # 15. Corrupt gzip rejected
    r15_bad = validate_prospective_payload_bars_and_quotes_r2_c1(b"\x1f\x8bNOT_VALID_GZIP_BYTES", is_gzipped=True, is_synthetic_fixture=True)
    _add("OOS-V15", "CORRUPT_GZIP_REJECTED", not r15_bad["valid"] and any("CORRUPT_GZIP" in v for v in r15_bad["violations"]), good_payload_res["valid"], ";".join(r15_bad["violations"]))

    # 16. Tampered hash-linked manifest detected
    reg_p = tmp_root / "synth_registry_v16.jsonl"
    reg = ProspectiveHashLinkedEscrowRegistryR2C1(reg_p)
    reg.append_validated_snapshot(base_good, freeze_timestamp_ms=freeze_ms)
    ch_ok = reg.verify_chain_integrity()["valid"]
    # Tamper recorded dataset_sha256 on disk without recomputing chain
    raw_txt = reg_p.read_text(encoding="utf-8").replace("d" * 64, "e" * 64)
    reg_p.write_text(raw_txt, encoding="utf-8")
    ch_bad = reg.verify_chain_integrity()
    _add("OOS-V16", "TAMPERED_MANIFEST_HASH_CHAIN_DETECTED", not ch_bad["valid"], ch_ok, ";".join(ch_bad["violations"]))

    # 17. Stale account fee tier claiming VERIFIED rejected
    s17_bad = dict(
        base_good,
        account_fee_tier_status="VERIFIED_ACCOUNT_FEE_TIER",
        account_fee_verified_utc_ms=base_good["capture_time_ms"] - 60 * 86_400_000,  # 60 days stale
    )
    r17_bad = validate_prospective_snapshot_contract_c1(s17_bad, freeze_timestamp_ms=freeze_ms)
    _add("OOS-V17", "STALE_ACCOUNT_FEE_TIER_REJECTED", not r17_bad["valid_for_escrow_admission"], good_val["valid_for_escrow_admission"], ";".join(r17_bad["violations"]))

    # 18. Missing / empty data payload rejected
    r18_bad = validate_prospective_payload_bars_and_quotes_r2_c1(_gz({"bars": [], "quotes": [], "trades": []}), is_synthetic_fixture=True)
    _add("OOS-V18", "MISSING_EMPTY_MARKET_DATA_REJECTED", not r18_bad["valid"], good_payload_res["valid"], ";".join(r18_bad["violations"]))

    # 19. Repeated ingestion idempotency (same content = NOOP; mutated content = PermissionError overwrite refused)
    reg19_p = tmp_root / "synth_registry_v19.jsonl"
    reg19 = ProspectiveHashLinkedEscrowRegistryR2C1(reg19_p)
    a1 = reg19.append_validated_snapshot(base_good, freeze_timestamp_ms=freeze_ms)
    a2 = reg19.append_validated_snapshot(base_good, freeze_timestamp_ms=freeze_ms)
    overwrite_blocked = False
    try:
        reg19.append_validated_snapshot(dict(base_good, bar_count=999), freeze_timestamp_ms=freeze_ms)
    except PermissionError:
        overwrite_blocked = True
    _add("OOS-V19", "REPEATED_INGESTION_IDEMPOTENCY_AND_NO_OVERWRITE", overwrite_blocked, (a1["action"] == "APPENDED_NEW_ENTRY" and a2["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP" and a2["record_count"] == 1), f"a1={a1['action']}, a2={a2['action']}")

    # 20. Secret / API key / cookie / account ID leakage in snapshot metadata rejected + synthetic fixture flag rejected
    s20_bad = dict(base_good, query_params={"symbol": "XAUUSDT", "api_key": "LEAKED_SECRET"})
    r20_bad = validate_prospective_snapshot_contract_c1(s20_bad, freeze_timestamp_ms=freeze_ms)
    s20_synth = dict(base_good, is_synthetic_fixture=True)
    r20_synth = validate_prospective_snapshot_contract_c1(s20_synth, freeze_timestamp_ms=freeze_ms)
    _add(
        "OOS-V20",
        "SECRET_REDACTION_AND_SYNTHETIC_FIXTURE_EXCLUSION",
        (not r20_bad["valid_for_escrow_admission"]) and (not r20_synth["valid_for_escrow_admission"]),
        good_val["valid_for_escrow_admission"],
        f"secret={r20_bad['violations']}; synth={r20_synth['violations']}",
    )

    passed = sum(1 for c in cases if c["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "fixture_provenance_disclosure": "ALL_20_TESTS_ARE_SYNTHETIC_VALIDATOR_ONLY_NOT_REAL_MARKET_DATA",
        "validates_real_market_data": False,
        "validates_validator_contract": True,
        "total_cases": len(cases),
        "passed_cases": passed,
        "failed_cases": len(cases) - passed,
        "all_passed": passed == len(cases),
        "cases": cases,
        "status": "PASS" if passed == len(cases) else "FAIL",
    }


def validate_prospective_snapshot_contract_c3(
    entry: Mapping[str, Any],
    *,
    raw_payload_bytes: Optional[bytes],
    capture_receipt: Optional[Any],
    require_live_real_oos: bool = True,
    freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
) -> Dict[str, Any]:
    """
    KH-02.R2.C3 hardened provenance contract preserving C1/C2 compatibility:
    NEVER accepts `is_synthetic_fixture=False`, `partition_role="FRESH_UNTOUCHED"`, or
    `observed_bid_ask_present=True` alone as proof of real OOS custody. Requires verified
    raw payload bytes + HMAC-verified TrustedCaptureReceiptC3 + raw-byte reparse check.
    """
    from services.kh_02_r2_c3_provenance_guard import evaluate_c3_prospective_admission

    return evaluate_c3_prospective_admission(
        entry,
        raw_payload_bytes=raw_payload_bytes,
        capture_receipt=capture_receipt,
        require_live_real_oos=require_live_real_oos,
        freeze_timestamp_ms=freeze_timestamp_ms,
        known_dev_shas=known_dev_shas,
    )
