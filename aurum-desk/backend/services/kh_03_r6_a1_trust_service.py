"""
aurum-desk/backend/services/kh_03_r6_a1_trust_service.py

AURUM DESK — KH-03.R6.A1 | TRUSTED PLAN CANONICALIZATION, DISPATCH AUTHORIZATION,
PARENT-AUTHORITATIVE WORKER ENVELOPES & INTERNAL SUPERVISOR CAPABILITY TOKENS
SPEC VERSION: KH-03.R6.A1-v1.0 | OFFLINE / LOOPBACK QA ONLY | EXTERNAL HARD-BLOCKED

Implements:
  - A1 hard policy constant `A1_EXTERNAL_CAPTURE_ENABLED = False` (non-overridable by CLI/env/dict)
  - F08: `canonicalize_r6_bound_plan_core()` (deep-frozen canonical serialization & SHA-256 digest)
  - Process-local non-serializable registries for authentic verifier-issued grants, leases,
    durable reservations, durable settlements, authorized dispatch requests, and trusted supervisor results
  - F12: `authorize_r6_a1_dispatch_request()` (strict contract + window + endpoint + loopback-only guard)
  - F15: `build_r6_a1_worker_envelope()` & `validate_r6_a1_worker_envelope_strict()`
  - F22: `_issue_r6_a1_trusted_supervisor_result()` (internal capability-gated minting ONLY by F19)

Threat Model Disclosure:
  - Protects ordinary public API, CLI, IPC, dict/dataclass-forgery, and replay boundaries inside
    the trusted Python process and across parent<->child subprocess boundaries.
  - Does NOT claim defense against arbitrary code execution / memory patching by a compromised
    OS administrator/root inside the same process.
  - In KH-03.R6.A1, NO code path can attest `RAW_OBSERVED_VERIFIED` or open external market sockets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import threading
import time
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    R6SourceContractRegistry,
    validate_r6_endpoint_contract,
)

SPEC_VERSION_KH03_R6_A1 = "KH-03.R6.A1-v1.0"
SCHEMA_VERSION_KH03_R6_A1 = "kh_03_r6_a1_v1"

BASELINE_REVIEWED_HEAD_SHA_R6_A1 = "28b252676a10aa21bec7ce14fbd9415db230c2f4"
PRIOR_R6_TESTED_EXECUTABLE_SHA = "529bdaf2e533d3616b1a8e0842c048da3b81126b"
PRIOR_R6_EVIDENCE_COMMIT_SHA = "c76bd74dedf2cfe42682cb4b9d68f11e6d585def"
PRIOR_R6_RUN_ID = "20261126T005509Z_529bdaf2e533"
PRIOR_R6_MANIFEST_26_SHA256 = "f7ed2c978778075d219217cd88ecdc881fde458173c6817172b289f00950320f"
PRIOR_R6_READBACK_28_SHA256 = "12a9a913c6b8923c453724b8e89ef6ee8076ccc96746c92ec38bea9131bae894"

# HARD POLICY SWITCH FOR KH-03.R6.A1: external market capture is unconditionally disabled.
A1_EXTERNAL_CAPTURE_ENABLED: bool = False

WORKER_ENVELOPE_SCHEMA_A1 = "kh_03_r6_a1_worker_envelope_v1"
TRUSTED_SUPERVISOR_SCHEMA_A1 = "kh_03_r6_a1_trusted_supervisor_v1"

BUDGET_FIELDS_A1: Tuple[str, ...] = (
    "connections",
    "requests",
    "retries",
    "subscriptions",
    "messages",
    "payload_bytes",
    "decoded_bytes",
    "pages",
    "disk_bytes",
    "records",
)

PARENT_LIMIT_REQUIRED_KEYS_A1: Tuple[str, ...] = (
    "remaining_deadline_sec",
    "max_payload_bytes",
    "max_decoded_bytes",
    "max_header_bytes",
    "max_requests",
    "max_retries",
    "max_connections",
    "max_subscriptions",
    "max_messages",
    "max_frame_bytes",
    "max_queue_items",
    "max_queue_bytes",
    "max_reconnects",
    "heartbeat_interval_sec",
    "pong_timeout_sec",
)

FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1 = frozenset({
    "remaining_deadline_sec",
    "deadline",
    "deadline_monotonic",
    "max_payload_bytes",
    "max_decoded_bytes",
    "max_header_bytes",
    "max_requests",
    "max_retries",
    "max_connections",
    "max_subscriptions",
    "max_messages",
    "max_frame_bytes",
    "max_queue_items",
    "max_queue_bytes",
    "max_reconnects",
    "heartbeat_interval_sec",
    "pong_timeout_sec",
    "external_capture_permitted",
    "allow_external",
    "caps",
    "limits",
    "parent_limits",
})

ALLOWED_LOOPBACK_HOSTS_A1 = frozenset({"127.0.0.1", "::1"})

_MAX_INT64_SAFE_A1 = (1 << 62) - 1
_A1_TRUST_HMAC_SECRET = secrets.token_bytes(32)
_A1_INTERNAL_CAPABILITY = object()  # Non-serializable process-local capability sentinel
_REG_LOCK = threading.Lock()

# Process-local non-serializable registries (keyed by secret token, never serialized to JSON!)
_VERIFIED_GRANT_TOKENS: Dict[str, Dict[str, Any]] = {}
_VERIFIED_LEASE_TOKENS: Dict[str, Dict[str, Any]] = {}
_RESERVATION_TOKENS: Dict[str, "R6DurableReservationReceipt"] = {}
_SETTLEMENT_TOKENS: Dict[str, "R6DurableSettlementReceipt"] = {}
_AUTHORIZED_REQUEST_TOKENS: Dict[str, "R6AuthorizedDispatchRequest"] = {}
_TRUSTED_SUPERVISOR_TOKENS: Dict[str, Dict[str, Any]] = {}


def _is_strict_int_a1(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_finite_num_a1(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _canonical_json_bytes_a1(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_bytes_a1(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_canonical_a1(obj: Any) -> str:
    return _sha256_bytes_a1(_canonical_json_bytes_a1(obj))


def _hmac_hex_a1(domain: str, digest_hex: str) -> str:
    return hmac.new(_A1_TRUST_HMAC_SECRET, f"{domain}|{digest_hex}".encode("utf-8"), hashlib.sha256).hexdigest()


def freeze_strict_int_caps_a1(
    caps_in: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> Tuple[Optional[Mapping[str, int]], Tuple[Tuple[str, int], ...], List[str]]:
    """
    Validates a budget caps mapping strictly (no floats, no bools, no NaN/Inf, no unknown keys,
    no negative or >INT64 values) and returns a deep-frozen MappingProxyType + sorted pairs tuple.
    """
    viols: List[str] = []
    if not isinstance(caps_in, Mapping):
        return None, (), ["CAPS_NOT_MAPPING"]

    extra = sorted(set(str(k) for k in caps_in.keys()) - set(BUDGET_FIELDS_A1))
    if extra:
        viols.append(f"UNKNOWN_BUDGET_KEYS:{','.join(extra)}")
    if require_complete:
        missing = sorted(set(BUDGET_FIELDS_A1) - set(str(k) for k in caps_in.keys()))
        if missing:
            viols.append(f"MISSING_BUDGET_KEYS:{','.join(missing)}")

    out: Dict[str, int] = {}
    for k in BUDGET_FIELDS_A1:
        if k not in caps_in:
            if not require_complete:
                out[k] = 0
            continue
        v = caps_in[k]
        if not _is_strict_int_a1(v) or int(v) < 0 or int(v) > _MAX_INT64_SAFE_A1:
            viols.append(f"INVALID_STRICT_INT_CAP:{k}:{v!r}")
        else:
            out[k] = int(v)

    if viols:
        return None, (), viols
    pairs = tuple((k, out[k]) for k in sorted(BUDGET_FIELDS_A1))
    return MappingProxyType(dict(pairs)), pairs, []


def complete_caps_vector_a1(partial: Optional[Mapping[str, int]] = None) -> Dict[str, int]:
    """Helper constructing a complete 10-field strict-int cap dict (zeros for unspecified dims)."""
    base = {k: 0 for k in BUDGET_FIELDS_A1}
    if partial:
        for k, v in partial.items():
            if k not in base:
                raise ValueError(f"UNKNOWN_BUDGET_KEY:{k}")
            if not _is_strict_int_a1(v) or int(v) < 0 or int(v) > _MAX_INT64_SAFE_A1:
                raise ValueError(f"INVALID_BUDGET_VALUE:{k}:{v!r}")
            base[k] = int(v)
    return base


# =====================================================================
# Process-local registrations for verified grant & lease authenticity
# =====================================================================
def register_verified_grant_token_a1(
    *,
    grant_identity_key: str,
    grant_payload_digest: str,
    key_fingerprint: str,
    trust_namespace: str,
    stage: str,
    tested_executable_sha: str,
    release_manifest_sha256: str,
    signature_sha256: str,
) -> str:
    token = secrets.token_hex(24)
    rec = {
        "grant_identity_key": str(grant_identity_key),
        "grant_payload_digest": str(grant_payload_digest),
        "key_fingerprint": str(key_fingerprint),
        "trust_namespace": str(trust_namespace),
        "stage": str(stage),
        "tested_executable_sha": str(tested_executable_sha),
        "release_manifest_sha256": str(release_manifest_sha256),
        "signature_sha256": str(signature_sha256),
        "registered_mono": time.monotonic(),
    }
    with _REG_LOCK:
        _VERIFIED_GRANT_TOKENS[token] = rec
    return token


def verify_registered_grant_authenticity_a1(verified_grant: Any) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    tok = getattr(verified_grant, "verification_token", "")
    if not isinstance(tok, str) or not tok:
        return False, ["UNREGISTERED_OR_FORGED_VERIFIED_GRANT_OBJECT"]
    with _REG_LOCK:
        rec = _VERIFIED_GRANT_TOKENS.get(tok)
    if rec is None:
        return False, ["FORGED_OR_UNKNOWN_GRANT_VERIFICATION_TOKEN"]
    checks = (
        ("grant_identity_key", rec["grant_identity_key"]),
        ("grant_payload_digest", rec["grant_payload_digest"]),
        ("key_fingerprint", rec["key_fingerprint"]),
        ("trust_namespace", rec["trust_namespace"]),
        ("stage", rec["stage"]),
        ("tested_executable_sha", rec["tested_executable_sha"]),
        ("release_manifest_sha256", rec["release_manifest_sha256"]),
    )
    for field_name, expected_val in checks:
        if getattr(verified_grant, field_name, None) != expected_val:
            viols.append(f"VERIFIED_GRANT_FIELD_DRIFT:{field_name}")
    return len(viols) == 0, viols


def register_verified_lease_token_a1(
    *,
    lease_id: str,
    grant_identity_key: str,
    grant_payload_digest: str,
    plan_digest: str,
    session_id: str,
    store_id: str,
    trust_namespace: str,
    stage: str,
    expires_utc_ms: int,
    claimed_generation: int,
    release_manifest_sha256: str,
    effective_caps: Mapping[str, int],
) -> str:
    token = secrets.token_hex(24)
    rec = {
        "lease_id": str(lease_id),
        "grant_identity_key": str(grant_identity_key),
        "grant_payload_digest": str(grant_payload_digest),
        "plan_digest": str(plan_digest),
        "session_id": str(session_id),
        "store_id": str(store_id),
        "trust_namespace": str(trust_namespace),
        "stage": str(stage),
        "expires_utc_ms": int(expires_utc_ms),
        "claimed_generation": int(claimed_generation),
        "release_manifest_sha256": str(release_manifest_sha256),
        "effective_caps": dict(effective_caps),
    }
    with _REG_LOCK:
        _VERIFIED_LEASE_TOKENS[token] = rec
    return token


def verify_registered_lease_authenticity_a1(lease: Any) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    tok = getattr(lease, "lease_token", "")
    if not isinstance(tok, str) or not tok:
        return False, ["UNREGISTERED_OR_FORGED_LEASE_OBJECT"]
    with _REG_LOCK:
        rec = _VERIFIED_LEASE_TOKENS.get(tok)
    if rec is None:
        return False, ["FORGED_OR_UNKNOWN_LEASE_TOKEN"]
    for field_name in (
        "lease_id",
        "grant_identity_key",
        "grant_payload_digest",
        "plan_digest",
        "session_id",
        "store_id",
        "trust_namespace",
        "stage",
        "expires_utc_ms",
        "claimed_generation",
        "release_manifest_sha256",
    ):
        if getattr(lease, field_name, None) != rec[field_name]:
            viols.append(f"LEASE_FIELD_DRIFT:{field_name}")
    got_caps = getattr(lease, "effective_caps", None)
    if not isinstance(got_caps, Mapping) or dict(got_caps) != rec["effective_caps"]:
        viols.append("LEASE_EFFECTIVE_CAPS_DRIFT")
    return len(viols) == 0, viols


# =====================================================================
# F08: Shared Canonical Bound Plan Core Serialization
# =====================================================================
def canonicalize_r6_bound_plan_core(
    *,
    plan_id: Any,
    stage: Any,
    purpose: Any,
    grant_identity_key: Any,
    grant_payload_digest: Any,
    tested_executable_sha: Any,
    release_manifest_sha256: Any,
    registry_digest: Any,
    venue: Any,
    api_family: Any,
    symbol: Any,
    product_type: Any,
    destination_store_id: Any,
    destination_root: Any,
    endpoints: Any,
    ws_channels: Any,
    window_start_ms: Any,
    window_end_ms: Any,
    max_session_duration_sec: Any,
    max_concurrency: Any,
    caps: Any,
    expires_utc_ms: Any,
    external_capture_permitted: Any,
    task_policy_digest: Any = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
    """
    F08: Single strict canonical serialization shared by `build_r6_bound_capture_plan` and
    `verify_r6_bound_plan_integrity`. Covers EVERY security-relevant field of `R6BoundCapturePlan`.
    Rejects floats/NaN/bool-as-int, unknown/missing caps, duplicates, relative/alias paths,
    and empty string identifiers.
    """
    viols: List[str] = []
    str_fields = {
        "plan_id": plan_id,
        "stage": stage,
        "purpose": purpose,
        "grant_identity_key": grant_identity_key,
        "grant_payload_digest": grant_payload_digest,
        "tested_executable_sha": tested_executable_sha,
        "release_manifest_sha256": release_manifest_sha256,
        "registry_digest": registry_digest,
        "venue": venue,
        "api_family": api_family,
        "symbol": symbol,
        "product_type": product_type,
        "destination_store_id": destination_store_id,
        "destination_root": destination_root,
    }
    clean_strs: Dict[str, str] = {}
    for k, v in str_fields.items():
        if not isinstance(v, str) or not v.strip():
            viols.append(f"PLAN_CORE_INVALID_STR:{k}")
        else:
            clean_strs[k] = v.strip()

    if "destination_root" in clean_strs:
        dp = Path(clean_strs["destination_root"])
        if not dp.is_absolute() or ".." in dp.parts:
            viols.append("PLAN_CORE_DESTINATION_ROOT_NOT_CANONICAL_ABSOLUTE")
        else:
            norm_dest = str(dp.resolve(strict=False))
            if norm_dest != clean_strs["destination_root"]:
                viols.append("PLAN_CORE_DESTINATION_ROOT_ALIAS_MISMATCH")

    if not isinstance(endpoints, (tuple, list)) or not endpoints or not all(isinstance(x, str) and x.strip() for x in endpoints):
        viols.append("PLAN_CORE_INVALID_ENDPOINTS")
        ep_list: List[str] = []
    else:
        ep_list = [x.strip() for x in endpoints]
        if len(set(ep_list)) != len(ep_list):
            viols.append("PLAN_CORE_DUPLICATE_ENDPOINTS")

    if not isinstance(ws_channels, (tuple, list)) or not all(isinstance(x, str) and x.strip() for x in ws_channels):
        viols.append("PLAN_CORE_INVALID_WS_CHANNELS")
        ws_list: List[str] = []
    else:
        ws_list = [x.strip() for x in ws_channels]
        if len(set(ws_list)) != len(ws_list):
            viols.append("PLAN_CORE_DUPLICATE_WS_CHANNELS")

    int_fields = {
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "max_session_duration_sec": max_session_duration_sec,
        "max_concurrency": max_concurrency,
        "expires_utc_ms": expires_utc_ms,
    }
    clean_ints: Dict[str, int] = {}
    for k, v in int_fields.items():
        if not _is_strict_int_a1(v) or int(v) <= 0 or int(v) > _MAX_INT64_SAFE_A1:
            viols.append(f"PLAN_CORE_INVALID_INT:{k}")
        else:
            clean_ints[k] = int(v)
    if "window_start_ms" in clean_ints and "window_end_ms" in clean_ints:
        if clean_ints["window_start_ms"] >= clean_ints["window_end_ms"]:
            viols.append("PLAN_CORE_INVALID_WINDOW_ORDER")

    if not isinstance(external_capture_permitted, bool):
        viols.append("PLAN_CORE_EXTERNAL_FLAG_NOT_BOOL")

    if not isinstance(task_policy_digest, str):
        viols.append("PLAN_CORE_TASK_POLICY_DIGEST_NOT_STR")

    _, cap_pairs, cap_viols = freeze_strict_int_caps_a1(caps, require_complete=True)
    viols.extend(cap_viols)

    if viols:
        return None, None, viols

    core = {
        "schema_version": "kh_03_r6_a1_bound_plan_core_v1",
        "plan_id": clean_strs["plan_id"],
        "stage": clean_strs["stage"],
        "purpose": clean_strs["purpose"],
        "grant_identity_key": clean_strs["grant_identity_key"],
        "grant_payload_digest": clean_strs["grant_payload_digest"],
        "tested_executable_sha": clean_strs["tested_executable_sha"],
        "release_manifest_sha256": clean_strs["release_manifest_sha256"],
        "registry_digest": clean_strs["registry_digest"],
        "venue": clean_strs["venue"],
        "api_family": clean_strs["api_family"],
        "symbol": clean_strs["symbol"],
        "product_type": clean_strs["product_type"],
        "destination_store_id": clean_strs["destination_store_id"],
        "destination_root": clean_strs["destination_root"],
        "endpoints": sorted(ep_list),
        "ws_channels": sorted(ws_list),
        "window_start_ms": clean_ints["window_start_ms"],
        "window_end_ms": clean_ints["window_end_ms"],
        "max_session_duration_sec": clean_ints["max_session_duration_sec"],
        "max_concurrency": clean_ints["max_concurrency"],
        "caps": [[k, v] for k, v in cap_pairs],
        "expires_utc_ms": clean_ints["expires_utc_ms"],
        "external_capture_permitted": bool(external_capture_permitted),
        "task_policy_digest": str(task_policy_digest),
    }
    return core, _sha256_canonical_a1(core), []


# =====================================================================
# Durable Reservation & Settlement Typed Receipts (F13, F20)
# =====================================================================
@dataclass(frozen=True)
class R6DurableReservationReceipt:
    attempt_id: str
    lease_id: str
    session_id: str
    grant_identity_key: str
    plan_digest: str
    request_type: str
    request_digest: str
    reserved_caps: Mapping[str, int]
    reserved_generation: int
    audit_entry_hash: str
    created_utc_ms: int
    receipt_digest: str
    receipt_token: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "grant_identity_key": self.grant_identity_key,
            "plan_digest": self.plan_digest,
            "request_type": self.request_type,
            "request_digest": self.request_digest,
            "reserved_caps": dict(self.reserved_caps),
            "reserved_generation": self.reserved_generation,
            "audit_entry_hash": self.audit_entry_hash,
            "created_utc_ms": self.created_utc_ms,
            "receipt_digest": self.receipt_digest,
        }


def issue_durable_reservation_receipt_a1(
    *,
    attempt_id: str,
    lease_id: str,
    session_id: str,
    grant_identity_key: str,
    plan_digest: str,
    request_type: str,
    request_digest: str,
    reserved_caps: Mapping[str, int],
    reserved_generation: int,
    audit_entry_hash: str,
    created_utc_ms: int,
) -> R6DurableReservationReceipt:
    frozen_caps, _, viols = freeze_strict_int_caps_a1(reserved_caps, require_complete=True)
    if viols or frozen_caps is None:
        raise ValueError(f"INVALID_RESERVATION_CAPS:{viols}")
    core = {
        "attempt_id": str(attempt_id),
        "lease_id": str(lease_id),
        "session_id": str(session_id),
        "grant_identity_key": str(grant_identity_key),
        "plan_digest": str(plan_digest),
        "request_type": str(request_type),
        "request_digest": str(request_digest),
        "reserved_caps": dict(frozen_caps),
        "reserved_generation": int(reserved_generation),
        "audit_entry_hash": str(audit_entry_hash),
        "created_utc_ms": int(created_utc_ms),
    }
    rdig = _sha256_canonical_a1(core)
    tok = secrets.token_hex(24)
    rec = R6DurableReservationReceipt(
        attempt_id=core["attempt_id"],
        lease_id=core["lease_id"],
        session_id=core["session_id"],
        grant_identity_key=core["grant_identity_key"],
        plan_digest=core["plan_digest"],
        request_type=core["request_type"],
        request_digest=core["request_digest"],
        reserved_caps=frozen_caps,
        reserved_generation=core["reserved_generation"],
        audit_entry_hash=core["audit_entry_hash"],
        created_utc_ms=core["created_utc_ms"],
        receipt_digest=rdig,
        receipt_token=tok,
    )
    with _REG_LOCK:
        _RESERVATION_TOKENS[tok] = rec
    return rec


def verify_durable_reservation_receipt_a1(receipt: Any) -> Tuple[bool, List[str]]:
    if not isinstance(receipt, R6DurableReservationReceipt):
        return False, ["INVALID_RESERVATION_RECEIPT_TYPE"]
    with _REG_LOCK:
        orig = _RESERVATION_TOKENS.get(receipt.receipt_token)
    if orig is None or orig is not receipt:
        return False, ["UNREGISTERED_OR_FORGED_RESERVATION_RECEIPT"]
    return True, []


@dataclass(frozen=True)
class R6DurableSettlementReceipt:
    attempt_id: str
    lease_id: str
    session_id: str
    grant_identity_key: str
    plan_digest: str
    request_digest: str
    outcome: str
    final_attempt_state: str
    reserved_caps: Mapping[str, int]
    known_caps: Mapping[str, int]
    uncertain_caps: Mapping[str, int]
    settled_generation: int
    audit_entry_hash: str
    settled_utc_ms: int
    raw_payload_sha256: str
    child_result_digest: str
    conservation_verified: bool
    quarantined: bool
    receipt_digest: str
    receipt_token: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "grant_identity_key": self.grant_identity_key,
            "plan_digest": self.plan_digest,
            "request_digest": self.request_digest,
            "outcome": self.outcome,
            "final_attempt_state": self.final_attempt_state,
            "reserved_caps": dict(self.reserved_caps),
            "known_caps": dict(self.known_caps),
            "uncertain_caps": dict(self.uncertain_caps),
            "settled_generation": self.settled_generation,
            "audit_entry_hash": self.audit_entry_hash,
            "settled_utc_ms": self.settled_utc_ms,
            "raw_payload_sha256": self.raw_payload_sha256,
            "child_result_digest": self.child_result_digest,
            "conservation_verified": self.conservation_verified,
            "quarantined": self.quarantined,
            "receipt_digest": self.receipt_digest,
        }


def issue_durable_settlement_receipt_a1(
    *,
    attempt_id: str,
    lease_id: str,
    session_id: str,
    grant_identity_key: str,
    plan_digest: str,
    request_digest: str,
    outcome: str,
    final_attempt_state: str,
    reserved_caps: Mapping[str, int],
    known_caps: Mapping[str, int],
    uncertain_caps: Mapping[str, int],
    settled_generation: int,
    audit_entry_hash: str,
    settled_utc_ms: int,
    raw_payload_sha256: str = "",
    child_result_digest: str = "",
    conservation_verified: bool,
    quarantined: bool,
) -> R6DurableSettlementReceipt:
    f_res, _, v1 = freeze_strict_int_caps_a1(reserved_caps, require_complete=True)
    f_kn, _, v2 = freeze_strict_int_caps_a1(known_caps, require_complete=True)
    f_un, _, v3 = freeze_strict_int_caps_a1(uncertain_caps, require_complete=True)
    if v1 or v2 or v3 or f_res is None or f_kn is None or f_un is None:
        raise ValueError(f"INVALID_SETTLEMENT_CAPS:{v1 + v2 + v3}")
    core = {
        "attempt_id": str(attempt_id),
        "lease_id": str(lease_id),
        "session_id": str(session_id),
        "grant_identity_key": str(grant_identity_key),
        "plan_digest": str(plan_digest),
        "request_digest": str(request_digest),
        "outcome": str(outcome),
        "final_attempt_state": str(final_attempt_state),
        "reserved_caps": dict(f_res),
        "known_caps": dict(f_kn),
        "uncertain_caps": dict(f_un),
        "settled_generation": int(settled_generation),
        "audit_entry_hash": str(audit_entry_hash),
        "settled_utc_ms": int(settled_utc_ms),
        "raw_payload_sha256": str(raw_payload_sha256),
        "child_result_digest": str(child_result_digest),
        "conservation_verified": bool(conservation_verified),
        "quarantined": bool(quarantined),
    }
    rdig = _sha256_canonical_a1(core)
    tok = secrets.token_hex(24)
    rec = R6DurableSettlementReceipt(
        attempt_id=core["attempt_id"],
        lease_id=core["lease_id"],
        session_id=core["session_id"],
        grant_identity_key=core["grant_identity_key"],
        plan_digest=core["plan_digest"],
        request_digest=core["request_digest"],
        outcome=core["outcome"],
        final_attempt_state=core["final_attempt_state"],
        reserved_caps=f_res,
        known_caps=f_kn,
        uncertain_caps=f_un,
        settled_generation=core["settled_generation"],
        audit_entry_hash=core["audit_entry_hash"],
        settled_utc_ms=core["settled_utc_ms"],
        raw_payload_sha256=core["raw_payload_sha256"],
        child_result_digest=core["child_result_digest"],
        conservation_verified=core["conservation_verified"],
        quarantined=core["quarantined"],
        receipt_digest=rdig,
        receipt_token=tok,
    )
    with _REG_LOCK:
        _SETTLEMENT_TOKENS[tok] = rec
    return rec


def verify_durable_settlement_receipt_a1(receipt: Any) -> Tuple[bool, List[str]]:
    if not isinstance(receipt, R6DurableSettlementReceipt):
        return False, ["INVALID_SETTLEMENT_RECEIPT_TYPE"]
    with _REG_LOCK:
        orig = _SETTLEMENT_TOKENS.get(receipt.receipt_token)
    if orig is None or orig is not receipt:
        return False, ["UNREGISTERED_OR_FORGED_SETTLEMENT_RECEIPT"]
    if not receipt.conservation_verified or receipt.quarantined:
        return False, ["SETTLEMENT_NOT_CONSERVED_OR_QUARANTINED"]
    return True, []


# =====================================================================
# F12: Exact Request Authorization & Endpoint Allowlist Builder
# =====================================================================
@dataclass(frozen=True)
class R6AuthorizedDispatchRequest:
    attempt_id: str
    session_id: str
    lease_id: str
    grant_identity_key: str
    plan_digest: str
    registry_digest: str
    endpoint_id: str
    contract_digest: str
    request_kind: str  # "rest_get" | "ws_session"
    partition: str
    qa_loopback_mode: bool
    canonical_contract_url: str
    dispatch_url: str
    params: Mapping[str, Any]
    subscribe_payload: Mapping[str, Any]
    requested_caps: Mapping[str, int]
    deadline_monotonic: float
    expires_utc_ms: int
    request_digest: str
    auth_token: str

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "grant_identity_key": self.grant_identity_key,
            "plan_digest": self.plan_digest,
            "registry_digest": self.registry_digest,
            "endpoint_id": self.endpoint_id,
            "contract_digest": self.contract_digest,
            "request_kind": self.request_kind,
            "partition": self.partition,
            "qa_loopback_mode": self.qa_loopback_mode,
            "canonical_contract_url": self.canonical_contract_url,
            "dispatch_url": self.dispatch_url,
            "params": dict(self.params),
            "subscribe_payload": dict(self.subscribe_payload),
            "requested_caps": dict(self.requested_caps),
            "expires_utc_ms": self.expires_utc_ms,
            "request_digest": self.request_digest,
        }


def authorize_r6_a1_dispatch_request(
    *,
    plan: Any,
    verified_grant: Any,
    lease: Any,
    registry: R6SourceContractRegistry,
    endpoint_id: str,
    attempt_id: str,
    partition: str,
    requested_caps: Mapping[str, int],
    deadline_monotonic: float,
    params: Optional[Mapping[str, Any]] = None,
    subscribe_payload: Optional[Mapping[str, Any]] = None,
    qa_loopback_mode: bool = False,
    qa_loopback_url: Optional[str] = None,
    actual_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    F12: Constructs a strictly bound, contract-validated `R6AuthorizedDispatchRequest`.
    Calls `validate_r6_endpoint_contract()` on EVERY attempt; enforces A1 hard-block on
    ALL non-loopback targets; rejects unauthorized paths, extra query params, URL credentials,
    proxies, private/trading ops, window drift, and unverified grants/leases/caps.
    """
    viols: List[str] = []
    now_dt = actual_now or datetime.now(timezone.utc)
    now_ms = int(now_dt.astimezone(timezone.utc).timestamp() * 1000)

    # Hard A1 external capture policy: NEVER permit non-loopback I/O in A1!
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        return {
            "authorized": False,
            "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "authorized_request": None,
            "violations": ["A1_EXTERNAL_CAPTURE_DISABLED_NO_NONLOOPBACK_DISPATCH"],
        }

    if not isinstance(registry, R6SourceContractRegistry) or registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
        viols.append("INVALID_OR_UNREADY_SOURCE_REGISTRY")

    ok_g, g_viols = verify_registered_grant_authenticity_a1(verified_grant)
    if not ok_g:
        viols.extend(g_viols)
    ok_l, l_viols = verify_registered_lease_authenticity_a1(lease)
    if not ok_l:
        viols.extend(l_viols)

    if getattr(plan, "grant_identity_key", None) != getattr(verified_grant, "grant_identity_key", None):
        viols.append("DISPATCH_PLAN_GRANT_IDENTITY_MISMATCH")
    if getattr(lease, "plan_digest", None) != getattr(plan, "plan_digest", None):
        viols.append("DISPATCH_LEASE_PLAN_DIGEST_MISMATCH")
    if getattr(lease, "grant_identity_key", None) != getattr(verified_grant, "grant_identity_key", None):
        viols.append("DISPATCH_LEASE_GRANT_MISMATCH")
    if getattr(plan, "registry_digest", None) != getattr(registry, "registry_digest", None):
        viols.append("DISPATCH_PLAN_REGISTRY_DIGEST_MISMATCH")

    if now_ms >= int(getattr(lease, "expires_utc_ms", 0)) or now_ms >= int(getattr(verified_grant, "expires_utc_ms", 0)):
        viols.append("DISPATCH_GRANT_OR_LEASE_EXPIRED")
    if not _is_finite_num_a1(deadline_monotonic) or float(deadline_monotonic) <= time.monotonic():
        viols.append("DISPATCH_DEADLINE_ALREADY_EXPIRED")

    if not isinstance(attempt_id, str) or not attempt_id.strip():
        viols.append("DISPATCH_MISSING_ATTEMPT_ID")

    if qa_loopback_mode:
        if partition != "QA_FIXTURE":
            viols.append(f"QA_LOOPBACK_MUST_USE_QA_FIXTURE_PARTITION:{partition}")
    else:
        if partition != "DEV_ONLY":
            viols.append(f"EXTERNAL_MUST_USE_DEV_ONLY_PARTITION:{partition}")

    if endpoint_id not in getattr(plan, "endpoints", ()):
        viols.append(f"ENDPOINT_NOT_IN_PLAN:{endpoint_id}")
    if endpoint_id not in getattr(verified_grant, "allowed_endpoints", ()):
        viols.append(f"ENDPOINT_NOT_IN_GRANT:{endpoint_id}")

    ep = registry.endpoints.get(endpoint_id) if isinstance(registry, R6SourceContractRegistry) else None
    if ep is None:
        return {
            "authorized": False,
            "status": "REJECTED_UNKNOWN_ENDPOINT_ID",
            "authorized_request": None,
            "violations": viols + [f"UNKNOWN_ENDPOINT_ID:{endpoint_id}"],
        }

    # Validate caps completely and check subset of plan caps
    frozen_caps, _, cap_viols = freeze_strict_int_caps_a1(requested_caps, require_complete=True)
    viols.extend(cap_viols)
    plan_caps = getattr(plan, "caps", {})
    if frozen_caps is not None:
        for k in BUDGET_FIELDS_A1:
            if frozen_caps[k] > int(plan_caps.get(k, 0)):
                viols.append(f"DISPATCH_CAP_EXCEEDS_PLAN:{k}:{frozen_caps[k]}>{plan_caps.get(k, 0)}")

    is_ws = ep.transport_kind == "WS_PUBLIC"
    request_kind = "ws_session" if is_ws else "rest_get"
    if frozen_caps is not None:
        if request_kind == "rest_get":
            if frozen_caps["requests"] < 1 or frozen_caps["payload_bytes"] < 1 or frozen_caps["decoded_bytes"] < 1:
                viols.append("REST_RESERVATION_ZERO_REQUIRED_CAP")
        else:
            if frozen_caps["connections"] < 1 or frozen_caps["subscriptions"] < 1 or frozen_caps["messages"] < 1 or frozen_caps["payload_bytes"] < 1 or frozen_caps["decoded_bytes"] < 1:
                viols.append("WS_RESERVATION_ZERO_REQUIRED_CAP")

    # Reject any callable or forbidden override keys in params / subscribe_payload
    clean_params: Dict[str, Any] = {}
    clean_sub: Dict[str, Any] = {}
    if params is not None:
        if not isinstance(params, Mapping):
            viols.append("DISPATCH_PARAMS_NOT_MAPPING")
        else:
            for k, v in params.items():
                if callable(v) or str(k) in FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1:
                    viols.append(f"DISPATCH_FORBIDDEN_PARAM:{k}")
                clean_params[str(k)] = v
    if subscribe_payload is not None:
        if not isinstance(subscribe_payload, Mapping):
            viols.append("DISPATCH_SUBSCRIBE_NOT_MAPPING")
        else:
            for k, v in subscribe_payload.items():
                if callable(v) or str(k) in FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1:
                    viols.append(f"DISPATCH_FORBIDDEN_SUBSCRIBE_KEY:{k}")
                clean_sub[str(k)] = v

    # Validate canonical Bitget Classic v2 contract first via validate_r6_endpoint_contract()!
    canonical_ep_url = f"{ep.scheme}://{ep.host}{ep.path}"
    grant_scope_map = {
        "api_family": getattr(verified_grant, "api_family", API_FAMILY_CLASSIC_V2),
        "allowed_endpoints": getattr(plan, "endpoints", ()),
        "allowed_ws_channels": getattr(plan, "ws_channels", ()),
        "symbol": getattr(plan, "symbol", APPROVED_SYMBOL_R6),
        "product_type": getattr(plan, "product_type", APPROVED_PRODUCT_TYPE_V2_R6),
        "window_start_ms": int(getattr(plan, "window_start_ms", 0)),
        "window_end_ms": int(getattr(plan, "window_end_ms", 0)),
    }
    if is_ws:
        if clean_params:
            viols.append("WS_DISPATCH_UNEXPECTED_REST_PARAMS")
        ws_op = str(clean_sub.get("op") or "").strip().lower()
        ws_args = clean_sub.get("args")
        ws_arg_params: Dict[str, Any] = {}
        if ws_op != "subscribe" or not isinstance(ws_args, list) or len(ws_args) != 1 or not isinstance(ws_args[0], Mapping):
            viols.append("WS_DISPATCH_INVALID_SUBSCRIBE_STRUCTURE")
            ws_ch = ""
        else:
            arg0 = ws_args[0]
            ws_arg_params = {str(k): v for k, v in arg0.items()}
            ws_ch = str(arg0.get("channel") or "").strip()
            if ws_ch not in getattr(plan, "ws_channels", ()):
                viols.append(f"WS_CHANNEL_NOT_IN_PLAN:{ws_ch}")
            if ws_ch not in getattr(verified_grant, "allowed_ws_channels", ()):
                viols.append(f"WS_CHANNEL_NOT_IN_GRANT:{ws_ch}")
            if str(arg0.get("instType") or "") != APPROVED_PRODUCT_TYPE_V2_R6 or str(arg0.get("instId") or "") != APPROVED_SYMBOL_R6:
                viols.append("WS_INSTRUMENT_MISMATCH")
            extra_arg_keys = sorted(set(arg0.keys()) - {"instType", "channel", "instId"})
            if extra_arg_keys:
                viols.append(f"WS_SUBSCRIBE_EXTRA_ARG_KEYS:{','.join(extra_arg_keys)}")
        c_check = validate_r6_endpoint_contract(
            registry,
            endpoint_id,
            ws_arg_params,
            grant_scope=grant_scope_map,
            url_override=qa_loopback_url if qa_loopback_mode else canonical_ep_url,
            method_or_op=ws_op or "subscribe",
            qa_loopback_mode=qa_loopback_mode,
        )
        if not c_check["valid"]:
            viols.extend(c_check["violations"])
    else:
        if clean_sub:
            viols.append("REST_DISPATCH_UNEXPECTED_WS_SUBSCRIBE")
        allowed_param_keys = set(ep.required_params) | set(ep.optional_params)
        extra_p = sorted(set(clean_params.keys()) - allowed_param_keys)
        if extra_p:
            viols.append(f"REST_UNAUTHORIZED_EXTRA_PARAMS:{','.join(extra_p)}")
        # Check symbol/productType & window bounds
        if "symbol" in clean_params and str(clean_params["symbol"]) != APPROVED_SYMBOL_R6:
            viols.append(f"REST_SYMBOL_MISMATCH:{clean_params['symbol']}")
        if "productType" in clean_params and str(clean_params["productType"]) != APPROVED_PRODUCT_TYPE_V2_R6:
            viols.append(f"REST_PRODUCT_TYPE_MISMATCH:{clean_params['productType']}")
        # Window checks for candle/funding queries
        plan_ws = int(getattr(plan, "window_start_ms", 0))
        plan_we = int(getattr(plan, "window_end_ms", 0))
        if "startTime" in clean_params:
            st_v = clean_params["startTime"]
            try:
                st_i = int(st_v) if (isinstance(st_v, str) and st_v.isdigit()) or _is_strict_int_a1(st_v) else -1
            except Exception:
                st_i = -1
            if st_i < plan_ws or st_i >= plan_we:
                viols.append(f"REST_START_TIME_OUTSIDE_PLAN_WINDOW:{st_v}")
        if "endTime" in clean_params:
            et_v = clean_params["endTime"]
            try:
                et_i = int(et_v) if (isinstance(et_v, str) and et_v.isdigit()) or _is_strict_int_a1(et_v) else -1
            except Exception:
                et_i = -1
            if et_i <= plan_ws or et_i > plan_we:
                viols.append(f"REST_END_TIME_OUTSIDE_PLAN_WINDOW:{et_v}")

        c_check = validate_r6_endpoint_contract(
            registry,
            endpoint_id,
            clean_params,
            grant_scope=grant_scope_map,
            url_override=qa_loopback_url if qa_loopback_mode else canonical_ep_url,
            method_or_op="GET",
            qa_loopback_mode=qa_loopback_mode,
        )
        if not c_check["valid"]:
            viols.extend(c_check["violations"])

    # Validate dispatch URL (strictly loopback 127.0.0.1 / ::1 in QA loopback mode; no credentials, no proxy, exact contract path!)
    dispatch_url = canonical_ep_url
    if qa_loopback_mode:
        if not qa_loopback_url or not isinstance(qa_loopback_url, str):
            viols.append("QA_LOOPBACK_MISSING_LOCAL_URL")
        else:
            p_url = urlsplit(qa_loopback_url)
            expected_scheme = "ws" if is_ws else "http"
            host_l = (p_url.hostname or "").lower()
            if p_url.scheme.lower() != expected_scheme:
                viols.append(f"QA_LOOPBACK_BAD_SCHEME:{p_url.scheme}")
            if host_l not in ALLOWED_LOOPBACK_HOSTS_A1:
                viols.append(f"QA_LOOPBACK_NONLOOPBACK_FORBIDDEN:{host_l}")
            if p_url.username is not None or p_url.password is not None:
                viols.append("URL_EMBEDDED_CREDENTIALS_FORBIDDEN")
            if p_url.port is None or p_url.port <= 0 or p_url.port > 65535:
                viols.append("QA_LOOPBACK_MISSING_PORT")
            if p_url.query or p_url.fragment:
                viols.append("QA_LOOPBACK_URL_MUST_NOT_CONTAIN_RAW_QUERY_OR_FRAGMENT")
            # Path must match exact contract path (no arbitrary path SSRF on loopback either!)
            if p_url.path != ep.path:
                viols.append(f"QA_LOOPBACK_PATH_MISMATCH:expected={ep.path}:got={p_url.path}")
            dispatch_url = qa_loopback_url

    if viols or frozen_caps is None:
        return {
            "authorized": False,
            "status": "REJECTED_DISPATCH_AUTHORIZATION",
            "authorized_request": None,
            "violations": viols,
        }

    req_core = {
        "schema_version": "kh_03_r6_a1_dispatch_request_v1",
        "attempt_id": attempt_id.strip(),
        "session_id": str(lease.session_id),
        "lease_id": str(lease.lease_id),
        "grant_identity_key": str(verified_grant.grant_identity_key),
        "plan_digest": str(plan.plan_digest),
        "registry_digest": str(registry.registry_digest),
        "endpoint_id": endpoint_id,
        "contract_digest": ep.content_digest,
        "request_kind": request_kind,
        "partition": partition,
        "qa_loopback_mode": bool(qa_loopback_mode),
        "canonical_contract_url": canonical_ep_url,
        "dispatch_url": dispatch_url,
        "params": clean_params,
        "subscribe_payload": clean_sub,
        "requested_caps": dict(frozen_caps),
        "expires_utc_ms": int(lease.expires_utc_ms),
    }
    req_digest = _sha256_canonical_a1(req_core)
    tok = secrets.token_hex(24)
    auth_req = R6AuthorizedDispatchRequest(
        attempt_id=req_core["attempt_id"],
        session_id=req_core["session_id"],
        lease_id=req_core["lease_id"],
        grant_identity_key=req_core["grant_identity_key"],
        plan_digest=req_core["plan_digest"],
        registry_digest=req_core["registry_digest"],
        endpoint_id=endpoint_id,
        contract_digest=ep.content_digest,
        request_kind=request_kind,
        partition=partition,
        qa_loopback_mode=bool(qa_loopback_mode),
        canonical_contract_url=canonical_ep_url,
        dispatch_url=dispatch_url,
        params=MappingProxyType(dict(clean_params)),
        subscribe_payload=MappingProxyType(dict(clean_sub)),
        requested_caps=frozen_caps,
        deadline_monotonic=float(deadline_monotonic),
        expires_utc_ms=int(lease.expires_utc_ms),
        request_digest=req_digest,
        auth_token=tok,
    )
    with _REG_LOCK:
        _AUTHORIZED_REQUEST_TOKENS[tok] = auth_req
    return {
        "authorized": True,
        "status": "DISPATCH_AUTHORIZED_QA_LOOPBACK" if qa_loopback_mode else "DISPATCH_AUTHORIZED",
        "authorized_request": auth_req,
        "violations": [],
    }


def verify_authorized_dispatch_request_a1(
    auth_req: Any,
    *,
    expected_lease: Any,
    expected_plan: Any,
    actual_now: Optional[datetime] = None,
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if not isinstance(auth_req, R6AuthorizedDispatchRequest):
        return False, ["UNREGISTERED_OR_RAW_DISPATCH_REQUEST_REJECTED"]
    with _REG_LOCK:
        orig = _AUTHORIZED_REQUEST_TOKENS.get(auth_req.auth_token)
    if orig is None or orig is not auth_req:
        return False, ["FORGED_OR_UNKNOWN_DISPATCH_REQUEST_TOKEN"]
    if not A1_EXTERNAL_CAPTURE_ENABLED and not auth_req.qa_loopback_mode:
        viols.append("A1_EXTERNAL_CAPTURE_DISABLED")
    if auth_req.lease_id != getattr(expected_lease, "lease_id", None):
        viols.append("DISPATCH_LEASE_ID_MISMATCH")
    if auth_req.session_id != getattr(expected_lease, "session_id", None):
        viols.append("DISPATCH_SESSION_ID_MISMATCH")
    if auth_req.plan_digest != getattr(expected_plan, "plan_digest", None):
        viols.append("DISPATCH_PLAN_DIGEST_MISMATCH")
    now_dt = actual_now or datetime.now(timezone.utc)
    now_ms = int(now_dt.astimezone(timezone.utc).timestamp() * 1000)
    if now_ms >= auth_req.expires_utc_ms:
        viols.append("DISPATCH_REQUEST_GRANT_EXPIRED_AFTER_WAIT")
    if time.monotonic() >= auth_req.deadline_monotonic:
        viols.append("DISPATCH_REQUEST_DEADLINE_EXPIRED_AFTER_WAIT")
    return len(viols) == 0, viols


# =====================================================================
# F15: Parent-Authoritative Worker Envelope Builder & Strict Validator
# =====================================================================
@dataclass(frozen=True)
class R6WorkerEnvelope:
    envelope_schema: str
    attempt_id: str
    session_id: str
    lease_id: str
    plan_digest: str
    request_digest: str
    policy_namespace: str  # "QA_LOOPBACK_ONLY"
    qa_loopback_mode: bool
    external_capture_permitted: bool
    parent_abs_deadline_monotonic: float
    parent_limits: Mapping[str, Any]
    request: Mapping[str, Any]
    envelope_digest: str

    def to_ipc_dict(self) -> Dict[str, Any]:
        return {
            "envelope_schema": self.envelope_schema,
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "plan_digest": self.plan_digest,
            "request_digest": self.request_digest,
            "policy_namespace": self.policy_namespace,
            "qa_loopback_mode": self.qa_loopback_mode,
            "external_capture_permitted": self.external_capture_permitted,
            "parent_abs_deadline_monotonic": self.parent_abs_deadline_monotonic,
            "parent_limits": dict(self.parent_limits),
            "request": dict(self.request),
            "envelope_digest": self.envelope_digest,
        }


def build_r6_a1_worker_envelope(
    *,
    lease: Any,
    plan: Any,
    reservation_receipt: R6DurableReservationReceipt,
    request_payload: Mapping[str, Any],
    request_digest: str,
    remaining_deadline_sec: float,
    parent_abs_deadline_monotonic: float,
    qa_loopback_mode: bool,
    max_header_bytes: int = 32_768,
    max_frame_bytes: Optional[int] = None,
    max_queue_items: int = 64,
    max_queue_bytes: Optional[int] = None,
    heartbeat_interval_sec: float = 20.0,
    pong_timeout_sec: float = 5.0,
) -> Dict[str, Any]:
    """
    F15: Builds a strict, versioned, parent-authoritative worker envelope for child IPC.
    Rejects ANY limit/deadline/cap/retry/reconnect override keys inside `request_payload`.
    Refuses external capture unconditionally in A1.
    """
    viols: List[str] = []
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        return {
            "valid": False,
            "status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "envelope": None,
            "violations": ["A1_EXTERNAL_CAPTURE_DISABLED_WORKER_ENVELOPE"],
        }

    ok_res, res_viols = verify_durable_reservation_receipt_a1(reservation_receipt)
    if not ok_res:
        viols.extend(res_viols)
    if getattr(lease, "lease_id", None) != reservation_receipt.lease_id:
        viols.append("ENVELOPE_RESERVATION_LEASE_MISMATCH")
    if getattr(plan, "plan_digest", None) != reservation_receipt.plan_digest:
        viols.append("ENVELOPE_RESERVATION_PLAN_MISMATCH")
    if str(request_digest) != reservation_receipt.request_digest:
        viols.append("ENVELOPE_RESERVATION_REQUEST_DIGEST_MISMATCH")

    if not _is_finite_num_a1(remaining_deadline_sec) or float(remaining_deadline_sec) <= 0.0:
        viols.append("ENVELOPE_NONPOSITIVE_REMAINING_DEADLINE")
    if not _is_finite_num_a1(parent_abs_deadline_monotonic):
        viols.append("ENVELOPE_INVALID_ABS_DEADLINE")

    # Reject ANY override keys in request_payload (no child request overrides!)
    if not isinstance(request_payload, Mapping):
        viols.append("ENVELOPE_REQUEST_NOT_MAPPING")
        clean_req: Dict[str, Any] = {}
    else:
        clean_req = {}
        for k, v in request_payload.items():
            ks = str(k)
            if ks in FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1:
                viols.append(f"CHILD_REQUEST_OVERRIDE_FORBIDDEN:{ks}")
            if callable(v):
                viols.append(f"CHILD_REQUEST_CALLABLE_FORBIDDEN:{ks}")
            clean_req[ks] = v

    res_caps = reservation_receipt.reserved_caps
    # Strict parent-authoritative limits strictly bounded by durable reservation!
    max_pay = int(res_caps["payload_bytes"])
    max_dec = int(res_caps["decoded_bytes"])
    eff_frame = min(max_pay, int(max_frame_bytes)) if max_frame_bytes is not None else min(max_pay, 65_536)
    eff_q_bytes = min(max_pay, int(max_queue_bytes)) if max_queue_bytes is not None else min(max_pay, 131_072)
    eff_q_items = max(1, min(int(max_queue_items), max(1, int(res_caps["messages"]) or 64)))

    parent_limits = {
        "remaining_deadline_sec": round(float(remaining_deadline_sec), 6),
        "max_payload_bytes": max_pay,
        "max_decoded_bytes": max_dec,
        "max_header_bytes": int(max_header_bytes),
        "max_requests": int(res_caps["requests"]),
        "max_retries": int(res_caps["retries"]),
        "max_connections": int(res_caps["connections"]),
        "max_subscriptions": int(res_caps["subscriptions"]),
        "max_messages": int(res_caps["messages"]),
        "max_frame_bytes": int(eff_frame),
        "max_queue_items": int(eff_q_items),
        "max_queue_bytes": int(eff_q_bytes),
        "max_reconnects": int(res_caps["retries"]) if reservation_receipt.request_type == "ws_session" else 0,
        "heartbeat_interval_sec": float(heartbeat_interval_sec),
        "pong_timeout_sec": float(pong_timeout_sec),
    }

    if viols:
        return {"valid": False, "status": "REJECTED_WORKER_ENVELOPE", "envelope": None, "violations": viols}

    core = {
        "envelope_schema": WORKER_ENVELOPE_SCHEMA_A1,
        "attempt_id": reservation_receipt.attempt_id,
        "session_id": reservation_receipt.session_id,
        "lease_id": reservation_receipt.lease_id,
        "plan_digest": reservation_receipt.plan_digest,
        "request_digest": reservation_receipt.request_digest,
        "policy_namespace": "QA_LOOPBACK_ONLY",
        "qa_loopback_mode": bool(qa_loopback_mode),
        "external_capture_permitted": False,  # Hard False in A1!
        "parent_abs_deadline_monotonic": round(float(parent_abs_deadline_monotonic), 6),
        "parent_limits": parent_limits,
        "request": clean_req,
    }
    env_dig = _sha256_canonical_a1(core)
    env = R6WorkerEnvelope(
        envelope_schema=core["envelope_schema"],
        attempt_id=core["attempt_id"],
        session_id=core["session_id"],
        lease_id=core["lease_id"],
        plan_digest=core["plan_digest"],
        request_digest=core["request_digest"],
        policy_namespace=core["policy_namespace"],
        qa_loopback_mode=core["qa_loopback_mode"],
        external_capture_permitted=core["external_capture_permitted"],
        parent_abs_deadline_monotonic=core["parent_abs_deadline_monotonic"],
        parent_limits=MappingProxyType(dict(parent_limits)),
        request=MappingProxyType(dict(clean_req)),
        envelope_digest=env_dig,
    )
    val_ok, val_viols = validate_r6_a1_worker_envelope_strict(env.to_ipc_dict())
    if not val_ok:
        return {"valid": False, "status": "REJECTED_WORKER_ENVELOPE", "envelope": None, "violations": val_viols}
    return {"valid": True, "status": "WORKER_ENVELOPE_BUILT", "envelope": env, "violations": []}


def validate_r6_a1_worker_envelope_strict(raw_msg: Any) -> Tuple[bool, List[str]]:
    """
    Strict envelope validation executed BOTH by parent pre-launch AND by child `--child-worker`
    before opening any socket. Blocks:
      - Missing/unknown top-level or parent_limits fields (ZERO silent defaults!)
      - ANY cap/deadline/retry/reconnect override keys inside `request`
      - Non-finite / bool-as-int / negative values
      - ANY external/non-loopback target in A1 (even with forged flags!)
      - Tampered `envelope_digest`
    """
    viols: List[str] = []
    if not isinstance(raw_msg, Mapping):
        return False, ["ENVELOPE_NOT_OBJECT"]

    required_top = {
        "envelope_schema",
        "attempt_id",
        "session_id",
        "lease_id",
        "plan_digest",
        "request_digest",
        "policy_namespace",
        "qa_loopback_mode",
        "external_capture_permitted",
        "parent_abs_deadline_monotonic",
        "parent_limits",
        "request",
        "envelope_digest",
    }
    extra_top = sorted(set(raw_msg.keys()) - required_top)
    missing_top = sorted(required_top - set(raw_msg.keys()))
    if extra_top:
        viols.append(f"ENVELOPE_UNKNOWN_TOP_KEYS:{','.join(extra_top)}")
    if missing_top:
        viols.append(f"ENVELOPE_MISSING_TOP_KEYS:{','.join(missing_top)}")
    if viols:
        return False, viols

    if raw_msg.get("envelope_schema") != WORKER_ENVELOPE_SCHEMA_A1:
        viols.append(f"ENVELOPE_BAD_SCHEMA:{raw_msg.get('envelope_schema')}")

    for sk in ("attempt_id", "session_id", "lease_id", "plan_digest", "request_digest", "policy_namespace", "envelope_digest"):
        sv = raw_msg.get(sk)
        if not isinstance(sv, str) or not sv.strip():
            viols.append(f"ENVELOPE_EMPTY_STR:{sk}")

    qa_loop = raw_msg.get("qa_loopback_mode")
    ext_perm = raw_msg.get("external_capture_permitted")
    if not isinstance(qa_loop, bool) or not isinstance(ext_perm, bool):
        viols.append("ENVELOPE_BOOL_FLAGS_INVALID")

    # Hard A1 policy: child worker NEVER allows external capture or non-QA namespace!
    if not A1_EXTERNAL_CAPTURE_ENABLED:
        if ext_perm is True or qa_loop is not True or raw_msg.get("policy_namespace") != "QA_LOOPBACK_ONLY":
            viols.append("BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED")

    plims = raw_msg.get("parent_limits")
    if not isinstance(plims, Mapping):
        viols.append("ENVELOPE_PARENT_LIMITS_NOT_OBJECT")
    else:
        extra_l = sorted(set(plims.keys()) - set(PARENT_LIMIT_REQUIRED_KEYS_A1))
        missing_l = sorted(set(PARENT_LIMIT_REQUIRED_KEYS_A1) - set(plims.keys()))
        if extra_l:
            viols.append(f"ENVELOPE_UNKNOWN_LIMIT_KEYS:{','.join(extra_l)}")
        if missing_l:
            viols.append(f"ENVELOPE_MISSING_LIMIT_KEYS:{','.join(missing_l)}")
        float_keys = {"remaining_deadline_sec", "heartbeat_interval_sec", "pong_timeout_sec"}
        for lk in PARENT_LIMIT_REQUIRED_KEYS_A1:
            if lk not in plims:
                continue
            lv = plims[lk]
            if lk in float_keys:
                if not _is_finite_num_a1(lv) or float(lv) <= 0.0:
                    viols.append(f"ENVELOPE_INVALID_LIMIT_FLOAT:{lk}")
            else:
                if not _is_strict_int_a1(lv) or int(lv) < 0 or int(lv) > _MAX_INT64_SAFE_A1:
                    viols.append(f"ENVELOPE_INVALID_LIMIT_INT:{lk}")

    req = raw_msg.get("request")
    if not isinstance(req, Mapping):
        viols.append("ENVELOPE_REQUEST_NOT_OBJECT")
    else:
        for rk in req.keys():
            if str(rk) in FORBIDDEN_REQUEST_OVERRIDE_KEYS_A1:
                viols.append(f"CHILD_REQUEST_OVERRIDE_FORBIDDEN:{rk}")
        kind = str(req.get("kind") or "")
        if kind == "rest_get":
            u = str(req.get("url") or "")
            pu = urlsplit(u)
            if (pu.hostname or "").lower() not in ALLOWED_LOOPBACK_HOSTS_A1 or pu.scheme.lower() != "http":
                viols.append("BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED")
            if isinstance(plims, Mapping):
                if int(plims.get("max_requests", 0)) < 1 or int(plims.get("max_payload_bytes", 0)) < 1 or int(plims.get("max_decoded_bytes", 0)) < 1:
                    viols.append("ENVELOPE_ZERO_CAP_REST_FORBIDDEN")
        elif kind == "ws_session":
            u = str(req.get("ws_url") or "")
            pu = urlsplit(u)
            if (pu.hostname or "").lower() not in ALLOWED_LOOPBACK_HOSTS_A1 or pu.scheme.lower() != "ws":
                viols.append("BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED")
            if isinstance(plims, Mapping):
                if int(plims.get("max_connections", 0)) < 1 or int(plims.get("max_subscriptions", 0)) < 1 or int(plims.get("max_messages", 0)) < 1:
                    viols.append("ENVELOPE_ZERO_CAP_WS_FORBIDDEN")
        elif kind == "qa_stall_noncooperative":
            if not qa_loop:
                viols.append("QA_STALL_ONLY_ALLOWED_IN_QA_LOOPBACK")
        else:
            viols.append(f"ENVELOPE_UNKNOWN_KIND:{kind}")

    # Recompute exact envelope_digest
    if not viols:
        core_recomp = {k: v for k, v in raw_msg.items() if k != "envelope_digest"}
        calc = _sha256_canonical_a1(core_recomp)
        if not hmac.compare_digest(calc, str(raw_msg.get("envelope_digest") or "")):
            viols.append("ENVELOPE_DIGEST_TAMPERED")

    return len(viols) == 0, viols


# =====================================================================
# F22: Internal Trusted Supervisor Result & Single-Use Capability Token
# =====================================================================
@dataclass(frozen=True)
class R6TrustedSupervisorResult:
    schema_version: str
    tested_executable_sha: str
    run_id: str
    session_id: str
    lease_id: str
    attempt_id: str
    plan_digest: str
    partition: str
    endpoint_id: str
    contract_digest: str
    request_digest: str
    started_utc_ms: int
    finished_utc_ms: int
    started_monotonic_sec: float
    finished_monotonic_sec: float
    child_pid: int
    child_create_time: float
    child_exit_code: int
    reaped_verified: bool
    containment_kind: str
    transport_kind: str
    qa_loopback_mode: bool
    tls_verified: bool
    attested_provenance_class: str  # Strictly "QA_LOOPBACK" in A1!
    raw_payload_sha256: str
    raw_payload_bytes_len: int
    decoded_payload_sha256: str
    decoded_payload_bytes_len: int
    known_counters: Mapping[str, int]
    settlement_generation: int
    settlement_audit_hash: str
    settlement_receipt_digest: str
    child_result_digest: str
    result_digest: str
    parent_hmac_hex: str
    capability_token: str

    def to_redacted_audit_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tested_executable_sha": self.tested_executable_sha,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "attempt_id": self.attempt_id,
            "plan_digest": self.plan_digest,
            "partition": self.partition,
            "endpoint_id": self.endpoint_id,
            "contract_digest": self.contract_digest,
            "request_digest": self.request_digest,
            "started_utc_ms": self.started_utc_ms,
            "finished_utc_ms": self.finished_utc_ms,
            "child_pid": self.child_pid,
            "child_create_time": self.child_create_time,
            "child_exit_code": self.child_exit_code,
            "reaped_verified": self.reaped_verified,
            "containment_kind": self.containment_kind,
            "transport_kind": self.transport_kind,
            "qa_loopback_mode": self.qa_loopback_mode,
            "tls_verified": self.tls_verified,
            "attested_provenance_class": self.attested_provenance_class,
            "raw_payload_sha256": self.raw_payload_sha256,
            "raw_payload_bytes_len": self.raw_payload_bytes_len,
            "decoded_payload_sha256": self.decoded_payload_sha256,
            "decoded_payload_bytes_len": self.decoded_payload_bytes_len,
            "known_counters": dict(self.known_counters),
            "settlement_generation": self.settlement_generation,
            "settlement_audit_hash": self.settlement_audit_hash,
            "settlement_receipt_digest": self.settlement_receipt_digest,
            "child_result_digest": self.child_result_digest,
            "result_digest": self.result_digest,
        }


def _issue_r6_a1_trusted_supervisor_result(
    *,
    capability: Any,
    tested_executable_sha: str,
    run_id: str,
    session_id: str,
    lease_id: str,
    attempt_id: str,
    plan_digest: str,
    partition: str,
    endpoint_id: str,
    contract_digest: str,
    request_digest: str,
    started_utc_ms: int,
    finished_utc_ms: int,
    started_monotonic_sec: float,
    finished_monotonic_sec: float,
    child_pid: int,
    child_create_time: float,
    child_exit_code: int,
    reaped_verified: bool,
    containment_kind: str,
    transport_kind: str,
    qa_loopback_mode: bool,
    tls_verified: bool,
    raw_payload_bytes: bytes,
    decoded_payload_bytes: bytes,
    known_counters: Mapping[str, int],
    settlement_receipt: R6DurableSettlementReceipt,
    child_result_digest: str,
) -> R6TrustedSupervisorResult:
    """
    F22: Internal capability-gated constructor invoked ONLY by `supervise_r6_session()` after
    actual owned child exit, verified OS reap, and verified durable settlement.
    In KH-03.R6.A1, can ONLY attest `QA_LOOPBACK` (NEVER `RAW_OBSERVED_VERIFIED`).
    """
    if capability is not _A1_INTERNAL_CAPABILITY:
        raise PermissionError("FORBIDDEN_DIRECT_CALL_TO_INTERNAL_TRUSTED_SUPERVISOR_ISSUER")
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        raise PermissionError("BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED")
    if not reaped_verified or int(child_exit_code) != 0 or int(child_pid) <= 0:
        raise ValueError("CANNOT_ISSUE_TRUSTED_RESULT_WITHOUT_REAPED_ZERO_EXIT_CHILD")

    ok_s, s_viols = verify_durable_settlement_receipt_a1(settlement_receipt)
    if not ok_s:
        raise ValueError(f"CANNOT_ISSUE_TRUSTED_RESULT_BAD_SETTLEMENT:{s_viols}")
    if settlement_receipt.attempt_id != attempt_id or settlement_receipt.lease_id != lease_id or settlement_receipt.session_id != session_id:
        raise ValueError("SETTLEMENT_RECEIPT_BINDING_MISMATCH")
    if settlement_receipt.final_attempt_state != "COMPLETED":
        raise ValueError(f"SETTLEMENT_NOT_COMPLETED:{settlement_receipt.final_attempt_state}")

    frozen_known, _, k_viols = freeze_strict_int_caps_a1(known_counters, require_complete=True)
    if k_viols or frozen_known is None:
        raise ValueError(f"INVALID_KNOWN_COUNTERS:{k_viols}")

    raw_b = bytes(raw_payload_bytes)
    dec_b = bytes(decoded_payload_bytes)
    raw_sha = _sha256_bytes_a1(raw_b)
    dec_sha = _sha256_bytes_a1(dec_b)

    # A1 policy: strictly QA_LOOPBACK (NEVER RAW_OBSERVED_VERIFIED even if tls_verified=True!)
    attested_class = "QA_LOOPBACK" if (qa_loopback_mode or not A1_EXTERNAL_CAPTURE_ENABLED) else "RAW_UNVERIFIED_REJECTED"

    core = {
        "schema_version": TRUSTED_SUPERVISOR_SCHEMA_A1,
        "tested_executable_sha": str(tested_executable_sha),
        "run_id": str(run_id),
        "session_id": str(session_id),
        "lease_id": str(lease_id),
        "attempt_id": str(attempt_id),
        "plan_digest": str(plan_digest),
        "partition": str(partition),
        "endpoint_id": str(endpoint_id),
        "contract_digest": str(contract_digest),
        "request_digest": str(request_digest),
        "started_utc_ms": int(started_utc_ms),
        "finished_utc_ms": int(finished_utc_ms),
        "started_monotonic_sec": round(float(started_monotonic_sec), 6),
        "finished_monotonic_sec": round(float(finished_monotonic_sec), 6),
        "child_pid": int(child_pid),
        "child_create_time": float(child_create_time),
        "child_exit_code": int(child_exit_code),
        "reaped_verified": bool(reaped_verified),
        "containment_kind": str(containment_kind),
        "transport_kind": str(transport_kind),
        "qa_loopback_mode": bool(qa_loopback_mode),
        "tls_verified": bool(tls_verified),
        "attested_provenance_class": attested_class,
        "raw_payload_sha256": raw_sha,
        "raw_payload_bytes_len": len(raw_b),
        "decoded_payload_sha256": dec_sha,
        "decoded_payload_bytes_len": len(dec_b),
        "known_counters": dict(frozen_known),
        "settlement_generation": int(settlement_receipt.settled_generation),
        "settlement_audit_hash": str(settlement_receipt.audit_entry_hash),
        "settlement_receipt_digest": str(settlement_receipt.receipt_digest),
        "child_result_digest": str(child_result_digest),
    }
    res_dig = _sha256_canonical_a1(core)
    mac_hex = _hmac_hex_a1("TRUSTED_SUPERVISOR_A1", res_dig)
    cap_tok = secrets.token_hex(24)

    tsr = R6TrustedSupervisorResult(
        schema_version=core["schema_version"],
        tested_executable_sha=core["tested_executable_sha"],
        run_id=core["run_id"],
        session_id=core["session_id"],
        lease_id=core["lease_id"],
        attempt_id=core["attempt_id"],
        plan_digest=core["plan_digest"],
        partition=core["partition"],
        endpoint_id=core["endpoint_id"],
        contract_digest=core["contract_digest"],
        request_digest=core["request_digest"],
        started_utc_ms=core["started_utc_ms"],
        finished_utc_ms=core["finished_utc_ms"],
        started_monotonic_sec=core["started_monotonic_sec"],
        finished_monotonic_sec=core["finished_monotonic_sec"],
        child_pid=core["child_pid"],
        child_create_time=core["child_create_time"],
        child_exit_code=core["child_exit_code"],
        reaped_verified=core["reaped_verified"],
        containment_kind=core["containment_kind"],
        transport_kind=core["transport_kind"],
        qa_loopback_mode=core["qa_loopback_mode"],
        tls_verified=core["tls_verified"],
        attested_provenance_class=core["attested_provenance_class"],
        raw_payload_sha256=raw_sha,
        raw_payload_bytes_len=len(raw_b),
        decoded_payload_sha256=dec_sha,
        decoded_payload_bytes_len=len(dec_b),
        known_counters=frozen_known,
        settlement_generation=core["settlement_generation"],
        settlement_audit_hash=core["settlement_audit_hash"],
        settlement_receipt_digest=core["settlement_receipt_digest"],
        child_result_digest=core["child_result_digest"],
        result_digest=res_dig,
        parent_hmac_hex=mac_hex,
        capability_token=cap_tok,
    )
    with _REG_LOCK:
        _TRUSTED_SUPERVISOR_TOKENS[cap_tok] = {
            "result": tsr,
            "raw_payload_bytes": raw_b,
            "decoded_payload_bytes": dec_b,
            "settlement_receipt": settlement_receipt,
            "minted_provenance_receipt_id": None,
            "consumed_for_custody": False,
        }
    return tsr


def verify_trusted_supervisor_result_a1(
    tsr: Any,
    *,
    expected_raw_payload_bytes: Optional[bytes] = None,
    settlement_receipt: Optional[R6DurableSettlementReceipt] = None,
    require_unminted: bool = False,
    require_unconsumed: bool = False,
) -> Tuple[bool, List[str], Optional[bytes]]:
    viols: List[str] = []
    if not isinstance(tsr, R6TrustedSupervisorResult):
        return False, ["FORGED_OR_MAPPING_SUPERVISOR_RESULT_REJECTED"], None

    with _REG_LOCK:
        entry = _TRUSTED_SUPERVISOR_TOKENS.get(tsr.capability_token)
    if entry is None or entry["result"] is not tsr:
        return False, ["UNREGISTERED_OR_COPIED_TRUSTED_SUPERVISOR_TOKEN"], None

    # Recompute canonical digest & HMAC
    core = {
        "schema_version": tsr.schema_version,
        "tested_executable_sha": tsr.tested_executable_sha,
        "run_id": tsr.run_id,
        "session_id": tsr.session_id,
        "lease_id": tsr.lease_id,
        "attempt_id": tsr.attempt_id,
        "plan_digest": tsr.plan_digest,
        "partition": tsr.partition,
        "endpoint_id": tsr.endpoint_id,
        "contract_digest": tsr.contract_digest,
        "request_digest": tsr.request_digest,
        "started_utc_ms": tsr.started_utc_ms,
        "finished_utc_ms": tsr.finished_utc_ms,
        "started_monotonic_sec": tsr.started_monotonic_sec,
        "finished_monotonic_sec": tsr.finished_monotonic_sec,
        "child_pid": tsr.child_pid,
        "child_create_time": tsr.child_create_time,
        "child_exit_code": tsr.child_exit_code,
        "reaped_verified": tsr.reaped_verified,
        "containment_kind": tsr.containment_kind,
        "transport_kind": tsr.transport_kind,
        "qa_loopback_mode": tsr.qa_loopback_mode,
        "tls_verified": tsr.tls_verified,
        "attested_provenance_class": tsr.attested_provenance_class,
        "raw_payload_sha256": tsr.raw_payload_sha256,
        "raw_payload_bytes_len": tsr.raw_payload_bytes_len,
        "decoded_payload_sha256": tsr.decoded_payload_sha256,
        "decoded_payload_bytes_len": tsr.decoded_payload_bytes_len,
        "known_counters": dict(tsr.known_counters),
        "settlement_generation": tsr.settlement_generation,
        "settlement_audit_hash": tsr.settlement_audit_hash,
        "settlement_receipt_digest": tsr.settlement_receipt_digest,
        "child_result_digest": tsr.child_result_digest,
    }
    recomp = _sha256_canonical_a1(core)
    if recomp != tsr.result_digest:
        viols.append("TRUSTED_SUPERVISOR_DIGEST_TAMPERED")
    exp_mac = _hmac_hex_a1("TRUSTED_SUPERVISOR_A1", recomp)
    if not hmac.compare_digest(tsr.parent_hmac_hex, exp_mac):
        viols.append("TRUSTED_SUPERVISOR_HMAC_FORGED")

    if not tsr.reaped_verified or tsr.child_exit_code != 0 or tsr.child_pid <= 0:
        viols.append("TRUSTED_SUPERVISOR_INVALID_REAP_OR_EXIT")
    if not A1_EXTERNAL_CAPTURE_ENABLED and tsr.attested_provenance_class != "QA_LOOPBACK":
        viols.append("A1_ONLY_QA_LOOPBACK_PROVENANCE_ALLOWED")

    stored_raw: bytes = entry["raw_payload_bytes"]
    if expected_raw_payload_bytes is not None:
        if len(expected_raw_payload_bytes) != len(stored_raw) or expected_raw_payload_bytes != stored_raw:
            viols.append("RAW_PAYLOAD_BYTES_MISMATCH_VS_SUPERVISOR_BUFFER")
        if _sha256_bytes_a1(expected_raw_payload_bytes) != tsr.raw_payload_sha256:
            viols.append("RAW_PAYLOAD_SHA256_MISMATCH_VS_SUPERVISOR")

    if settlement_receipt is not None:
        ok_s, s_v = verify_durable_settlement_receipt_a1(settlement_receipt)
        if not ok_s:
            viols.extend(s_v)
        elif settlement_receipt is not entry["settlement_receipt"] or settlement_receipt.receipt_digest != tsr.settlement_receipt_digest:
            viols.append("SETTLEMENT_RECEIPT_MISMATCH_VS_SUPERVISOR")

    if require_unminted and entry["minted_provenance_receipt_id"] is not None:
        viols.append(f"TRUSTED_SUPERVISOR_TOKEN_ALREADY_MINTED:{entry['minted_provenance_receipt_id']}")
    if require_unconsumed and entry["consumed_for_custody"] is True:
        viols.append("TRUSTED_SUPERVISOR_TOKEN_ALREADY_CONSUMED_FOR_CUSTODY")

    return len(viols) == 0, viols, stored_raw


def bind_provenance_receipt_to_trusted_token_a1(capability_token: str, receipt_id: str) -> bool:
    with _REG_LOCK:
        entry = _TRUSTED_SUPERVISOR_TOKENS.get(capability_token)
        if entry is None or entry["minted_provenance_receipt_id"] is not None:
            return False
        entry["minted_provenance_receipt_id"] = str(receipt_id)
        return True


def verify_and_consume_custody_token_a1(capability_token: str, expected_receipt_id: str, *, consume: bool = False) -> Tuple[bool, List[str]]:
    with _REG_LOCK:
        entry = _TRUSTED_SUPERVISOR_TOKENS.get(capability_token)
        if entry is None:
            return False, ["UNKNOWN_OR_EXPIRED_SUPERVISOR_CAPABILITY_TOKEN"]
        if entry["minted_provenance_receipt_id"] != expected_receipt_id:
            return False, ["PROVENANCE_RECEIPT_ID_NOT_BOUND_TO_SUPERVISOR_TOKEN"]
        if entry["consumed_for_custody"]:
            return False, ["SUPERVISOR_TOKEN_ALREADY_CONSUMED_SINGLE_USE_REPLAY_DENIED"]
        if consume:
            entry["consumed_for_custody"] = True
        return True, []


def get_trusted_supervisor_decoded_bytes_a1(tsr: R6TrustedSupervisorResult) -> Optional[bytes]:
    with _REG_LOCK:
        entry = _TRUSTED_SUPERVISOR_TOKENS.get(tsr.capability_token)
        if entry is None or entry["result"] is not tsr:
            return None
        return bytes(entry["decoded_payload_bytes"])
