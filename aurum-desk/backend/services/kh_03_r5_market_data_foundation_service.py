"""
aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py

AURUM DESK — KH-03.R5 | OBSERVED MARKET DATA FOUNDATION & EXECUTION FIDELITY
SPEC VERSION: KH-03.R5-v1.0 | ADDITIVE READ-ONLY FOUNDATION | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Implements §04 functions 1..10:
  1. build_r5_source_capability_inventory
  2. validate_r5_operator_capture_scope
  3. plan_r5_capture_batches
  4. validate_r5_instrument_metadata
  5. validate_r5_ohlcv_stream
  6. validate_r5_bbo_stream
  7. validate_r5_funding_history
  8. validate_r5_owner_fee_confirmation
  9. build_r5_market_data_coverage_report
 10. build_r5_execution_fidelity_register

Non-Negotiable Safety Contract:
  - Strictly additive; modifies ZERO production, signal, Entry/SL/TP, risk, fee, or prior sealed files.
  - Default authorization fails closed (`CAPTURE_BLOCKED_OWNER_AUTHORIZATION`); CLI flags, env bools,
    QA tokens, or prompt text NEVER prove human operator capture approval.
  - All money/price/rate/tick/fee arithmetic uses exact `Decimal`; NaN/Inf/bool rejected fail-closed.
  - Never treats QA fixtures, modeled spreads, or generic public rates as observed account/market evidence.
  - Never designates newly captured or historical extended data as fresh untouched OOS.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import threading
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo

from services.kh_02_r2_c2_public_capture import (
    FORBIDDEN_URL_TOKENS_C2,
    GRANULARITY_TO_MS_C2,
    OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2,
)
from services.kh_02_r2_c3_provenance_guard import (
    MAX_DECOMPRESSED_BODY_BYTES_C3,
    MAX_RAW_HTTP_BODY_BYTES_C3,
    OFFICIAL_BITGET_PUBLIC_HOST_C3,
    OFFICIAL_BITGET_PUBLIC_PATHS_C3,
    safe_bounded_decompress_if_gzip_c3,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    CANONICAL_DATASET_SHA256_KH02,
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED,
    scan_for_forbidden_secrets_r2_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03_R5 = "KH-03.R5-v1.0"
SCHEMA_VERSION_KH03_R5 = "kh_03_r5_market_data_foundation_v1"
SPEC_VERSION_KH03_R5_C1 = "KH-03.R5.C1-v1.0"
SCHEMA_VERSION_KH03_R5_C1 = "kh_03_r5_c1_capture_safety_v1"
SPEC_VERSION_KH03_R5_C2 = "KH-03.R5.C2-v1.0"
SCHEMA_VERSION_KH03_R5_C2 = "kh_03_r5_c2_technical_closure_v1"
_MAX_GRANT_LEDGER_ENTRIES_C2 = 65536

NY_TZ = ZoneInfo("America/New_York")
STRICT_PREFLIGHT_DISK_MIN_GIB_R5 = max(20.5, float(STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED))
STRICT_RUNTIME_DISK_MIN_GIB_R5 = 20.0
ADVISORY_DISK_TARGET_GIB_R5 = 25.0
MAX_GLOBAL_CONCURRENCY_R5 = 4
DEFAULT_MAX_PAGE_BYTES_R5 = MAX_RAW_HTTP_BODY_BYTES_C3
DEFAULT_MAX_DECOMPRESSED_BYTES_R5 = MAX_DECOMPRESSED_BODY_BYTES_C3
HARD_MAX_REQUESTS_R5_C1 = 500
HARD_MAX_TOTAL_BYTES_R5_C1 = 50 * 1024 * 1024
HARD_MAX_DURATION_SEC_R5_C1 = 3600
HARD_MAX_PAGES_R5_C1 = 500
HARD_MAX_RETRIES_R5_C1 = 3
HARD_MIN_REQUEST_INTERVAL_MS_R5_C1 = 50
ALLOWED_CAPTURE_PURPOSES_R5_C1: Tuple[str, ...] = ("DEVELOPMENT_CAPTURE", "QA_FIXTURE")
ALLOWED_PRODUCT_TYPES_R5_C1: Tuple[str, ...] = ("USDT-FUTURES",)
FORBIDDEN_OOS_SCOPE_TOKENS_R5_C1: Tuple[str, ...] = (
    "oos",
    "oos_escrow",
    "escrow_oos",
    "real_oos",
    "untouched_oos",
    "prospective_oos_escrow",
)

CAPTURE_SAFETY_THREAT_MODEL_R5_C1: Dict[str, Any] = {
    "spec_version": SPEC_VERSION_KH03_R5_C1,
    "genuine_owner_attestation_status": "BLOCKED_NO_EXTERNAL_VERIFIER_CONFIGURED",
    "prevents": [
        "Caller-constructed bare R5OperatorGrant or dict trust_context boolean claiming human owner approval",
        "Scope mismatch between authorization grant, planner budget, and capture dispatch (symbol, product_type, endpoints, window, budgets, run_id, destination)",
        "Plan mutation between planning, enqueue, and dispatch, including mutation followed by recomputing public plan_digest_sha256",
        "Grant expiry between plan creation and rate-limit/retry dispatch boundary",
        "CLI flags, environment variables, prompt text, or QA fixture grants being promoted to live network capture",
        "Missing, None, empty, NaN/Inf, bool/string, stale, future, or wrong-volume resource telemetry failing open",
        "Post-wait deadline/cancel/expiry race and shortening server Retry-After below requested wait",
    ],
    "cannot_prove_within_python_process": [
        "Arbitrary same-process Python code execution patching interpreter globals, module internals, or memory",
        "Compromised OS clock, filesystem, TLS trust store, or host process memory",
        "Genuine out-of-band human owner identity without a separate external cryptographic/signed attestation verifier (hence live capture remains BLOCKED by default)",
        "Zero wire bytes in flight once a blocking stdlib socket read inside asyncio.to_thread has already started",
    ],
}
SUPPORTED_TIMEFRAME_MS_R5: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "1D": 86_400_000,
}
EVIDENCE_CLASSES_R5: Tuple[str, ...] = (
    "OBSERVED_VERIFIED",
    "OWNER_ATTESTED",
    "DERIVED_FROM_OBSERVED",
    "MODELED",
    "QA_FIXTURE",
    "MISSING",
    "UNVERIFIED",
)
FORBIDDEN_FEE_SENSITIVE_KEYS: Tuple[str, ...] = (
    "api_key",
    "apikey",
    "api_secret",
    "secret_key",
    "passphrase",
    "password",
    "authorization",
    "bearer",
    "cookie",
    "account_id",
    "uid",
    "wallet_balance",
    "equity_usd",
    "private_key",
    "session_token",
)


def _dec(val: Any, *, field: str = "value") -> Decimal:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_DECIMAL:{field}:{val!r}")
    if isinstance(val, float) and not math.isfinite(val):
        raise ValueError(f"NONFINITE_DECIMAL:{field}:{val!r}")
    try:
        d = Decimal(str(val).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"UNPARSEABLE_DECIMAL:{field}:{val!r}") from exc
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL:{field}:{val!r}")
    return d


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _to_utc_ms(val: Any, *, field: str = "timestamp") -> int:
    """Strictly requires UTC epoch milliseconds (>= 1e12 and < 1e14) to reject seconds/nanoseconds confusion."""
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_TS_MS:{field}:{val!r}")
    if isinstance(val, str):
        s = val.strip()
        if not s.isdigit():
            raise ValueError(f"NON_INTEGER_TS_MS:{field}:{val!r}")
        iv = int(s)
    elif isinstance(val, int):
        iv = val
    else:
        raise ValueError(f"INVALID_TS_TYPE:{field}:{type(val).__name__}")
    if iv < 1_000_000_000_000 or iv >= 10_000_000_000_000:
        raise ValueError(f"TS_UNIT_NOT_UTC_MS:{field}:{iv}")
    return iv


def _ny_date_from_utc_ms(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt_utc.astimezone(NY_TZ).strftime("%Y-%m-%d")


def _tick_aligned(px: Decimal, tick: Decimal) -> bool:
    if tick <= Decimal("0"):
        return False
    rem = px % tick
    return rem == Decimal("0") or abs(rem) <= Decimal("1e-9") or abs(tick - rem) <= Decimal("1e-9")


def _nearest_quantile(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(float(v) for v in vals)
    idx = max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))
    return round(s[idx], 4)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class R5OperatorGrant:
    """
    Bounded operator scope control object (NOT by itself proof of human approval!).
    Live public capture requires an independent verified attestation from a genuine
    `R5OwnerAttestationVerifier` (default verifier ALWAYS denies with
    `BLOCKED_OWNER_AUTHORIZATION`). Plain dict flags in `trust_context` NEVER prove
    human operator approval.
    """
    grant_id: str
    issued_by: str
    purpose: str
    symbol: str
    product_type: str
    allowed_endpoints: Tuple[str, ...]
    window_start_ms: int
    window_end_ms: int
    max_requests: int
    max_total_bytes: int
    max_duration_sec: int
    expires_utc_ms: int
    run_id: str
    is_qa_fixture_grant: bool = False
    origin_kind: str = "UNVERIFIED_OBJECT"
    destination_scope: Optional[str] = None
    max_pages: Optional[int] = None
    max_response_bytes: Optional[int] = None
    max_retries_per_request: Optional[int] = None
    max_concurrency: Optional[int] = None
    min_request_interval_ms: Optional[int] = None


@dataclass(frozen=True)
class R5AttestationVerificationResult:
    """Result returned by an independent `R5OwnerAttestationVerifier` at the trust boundary."""

    verified: bool
    status: str
    reason: str
    verifier_id: str
    grant_id: str
    attested_at_utc_ms: Optional[int] = None


class R5OwnerAttestationVerifier(Protocol):
    """Trust-boundary interface for out-of-band human owner authorization verification."""

    def verify_owner_attestation(
        self,
        grant: R5OperatorGrant,
        effective_scope: Mapping[str, Any],
        now_utc: datetime,
        trust_context: Mapping[str, Any],
    ) -> R5AttestationVerificationResult:
        ...


class DefaultDenyOwnerAttestationVerifier:
    """
    Default fail-closed verifier. Because no external cryptographic owner attestation
    store is configured in KH-03.R5.C1, live public capture is ALWAYS denied with
    `BLOCKED_OWNER_AUTHORIZATION` regardless of any caller-provided dict flags.
    """

    verifier_id: str = "DEFAULT_DENY_NO_EXTERNAL_ATTESTATION_VERIFIER"

    def verify_owner_attestation(
        self,
        grant: R5OperatorGrant,
        effective_scope: Mapping[str, Any],
        now_utc: datetime,
        trust_context: Mapping[str, Any],
    ) -> R5AttestationVerificationResult:
        return R5AttestationVerificationResult(
            verified=False,
            status="BLOCKED_OWNER_AUTHORIZATION",
            reason="NO_GENUINE_OUT_OF_BAND_OWNER_ATTESTATION_VERIFIER_CONFIGURED",
            verifier_id=self.verifier_id,
            grant_id=str(getattr(grant, "grant_id", "") or ""),
            attested_at_utc_ms=None,
        )


_DEFAULT_DENY_ATTESTATION_VERIFIER = DefaultDenyOwnerAttestationVerifier()
_SCOPE_BINDING_KEY_C1: bytes = secrets.token_bytes(32)
_SCOPE_BINDING_PID_C1: int = os.getpid()
_MAX_BINDING_REGISTRY_C1: int = 4096


class _NonSerializableBindingTokenC1:
    """Process-local non-copyable, non-serializable capability token for scope/plan binding."""

    __slots__ = ("_nonce",)

    def __init__(self, nonce: str) -> None:
        object.__setattr__(self, "_nonce", nonce)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("BINDING_TOKEN_IMMUTABLE")

    def __copy__(self) -> Any:
        raise TypeError("BINDING_TOKEN_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("BINDING_TOKEN_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("BINDING_TOKEN_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("BINDING_TOKEN_NON_SERIALIZABLE")


@dataclass(frozen=True)
class R5TrustedScopeReceipt:
    """Immutable, process-local scope approval receipt bound to exact effective scope & grant identity."""

    receipt_id: str
    spec_version: str
    mode: str
    authorization_status: str
    live_network_permitted: bool
    grant_id: str
    issued_by: str
    purpose: str
    run_id: str
    expires_utc_ms: int
    effective_scope: Dict[str, Any]
    scope_digest_sha256: str
    verifier_id: str
    worker_pid: int
    receipt_mac_hex: str
    grant_identity_key: str = ""
    grant_payload_digest: str = ""
    grant_namespace: str = "QA_FIXTURE"
    _token: Optional[_NonSerializableBindingTokenC1] = None

    def __copy__(self) -> Any:
        raise TypeError("TRUSTED_SCOPE_RECEIPT_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("TRUSTED_SCOPE_RECEIPT_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("TRUSTED_SCOPE_RECEIPT_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("TRUSTED_SCOPE_RECEIPT_NON_SERIALIZABLE")


@dataclass(frozen=True)
class R5TrustedPlanReceipt:
    """Immutable, process-local plan binding receipt preventing post-plan/rehashed mutations."""

    plan_receipt_id: str
    scope_receipt_id: str
    scope_digest_sha256: str
    plan_digest_sha256: str
    spec_version: str
    partition_role: str
    live_network_permitted: bool
    grant_id: str
    run_id: str
    expires_utc_ms: int
    destination_scope: Optional[str]
    immutable_plan_snapshot_json: str
    worker_pid: int
    receipt_mac_hex: str
    grant_identity_key: str = ""
    grant_payload_digest: str = ""
    grant_namespace: str = "QA_FIXTURE"
    _token: Optional[_NonSerializableBindingTokenC1] = None

    def __copy__(self) -> Any:
        raise TypeError("TRUSTED_PLAN_RECEIPT_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("TRUSTED_PLAN_RECEIPT_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("TRUSTED_PLAN_RECEIPT_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("TRUSTED_PLAN_RECEIPT_NON_SERIALIZABLE")


@dataclass(frozen=True)
class R5GrantSessionLeaseC2:
    """Immutable non-copyable lease representing one claimed one-shot grant capture session."""

    lease_id: str
    grant_identity_key: str
    grant_payload_digest: str
    grant_id: str
    grant_namespace: str
    session_id: str
    scope_receipt_id: str
    plan_receipt_id: str
    max_requests: int
    max_total_bytes: int
    worker_pid: int
    _token: Optional[_NonSerializableBindingTokenC1] = None

    def __copy__(self) -> Any:
        raise TypeError("GRANT_SESSION_LEASE_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("GRANT_SESSION_LEASE_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("GRANT_SESSION_LEASE_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("GRANT_SESSION_LEASE_NON_SERIALIZABLE")


def _canonical_grant_identity_c2(
    verified_grant: R5OperatorGrant,
    verifier_identity: str,
) -> Tuple[str, str, str]:
    """
    Derives stable logical grant identity `(grant_identity_key, canonical_payload_digest, namespace)`.
    Identity depends ONLY on `(namespace, verifier_identity, issued_by, grant_id)` — NOT receipt ID,
    plan ID, nonce, or session ID. Reissuing a receipt or replanning yields the exact same key;
    reusing the same key with altered grant payload fails closed with `GRANT_IDENTITY_PAYLOAD_CONFLICT`.
    """
    is_qa = bool(verified_grant.is_qa_fixture_grant) and str(verified_grant.purpose).strip() == "QA_FIXTURE"
    namespace = "QA_FIXTURE" if is_qa else "LIVE_DEV"
    ver_id = str(verifier_identity).strip()
    issuer = str(verified_grant.issued_by).strip()
    gid = str(verified_grant.grant_id).strip()
    grant_key = canonical_hash({
        "namespace": namespace,
        "verifier_identity": ver_id,
        "issued_by": issuer,
        "grant_id": gid,
    })
    payload_canonical = {
        "namespace": namespace,
        "verifier_identity": ver_id,
        "issued_by": issuer,
        "grant_id": gid,
        "purpose": str(verified_grant.purpose).strip(),
        "symbol": str(verified_grant.symbol).strip(),
        "product_type": str(verified_grant.product_type).strip(),
        "allowed_endpoints": sorted(str(e).strip() for e in (verified_grant.allowed_endpoints or ())),
        "window_start_ms": int(verified_grant.window_start_ms),
        "window_end_ms": int(verified_grant.window_end_ms),
        "max_requests": int(verified_grant.max_requests),
        "max_total_bytes": int(verified_grant.max_total_bytes),
        "max_duration_sec": int(verified_grant.max_duration_sec),
        "expires_utc_ms": int(verified_grant.expires_utc_ms),
        "run_id": str(verified_grant.run_id).strip(),
        "is_qa_fixture_grant": bool(verified_grant.is_qa_fixture_grant),
        "origin_kind": str(verified_grant.origin_kind).strip(),
        "max_pages": int(verified_grant.max_pages) if verified_grant.max_pages is not None else None,
        "max_response_bytes": int(verified_grant.max_response_bytes) if verified_grant.max_response_bytes is not None else None,
        "max_retries_per_request": int(verified_grant.max_retries_per_request) if verified_grant.max_retries_per_request is not None else None,
        "max_concurrency": int(verified_grant.max_concurrency) if verified_grant.max_concurrency is not None else None,
        "min_request_interval_ms": int(verified_grant.min_request_interval_ms) if verified_grant.min_request_interval_ms is not None else None,
        "destination_scope": str(verified_grant.destination_scope).strip() if verified_grant.destination_scope is not None else None,
    }
    return grant_key, canonical_hash(payload_canonical), namespace


class R5GrantConsumptionLedgerC2:
    """
    Fail-closed one-shot bounded session consumption ledger (KH-03.R5.C2 P1-F).
    - One verified logical grant authorizes at most ONE capture session (no sequential/concurrent
      replay, no replanning budget multiplication, no scope-receipt reissue reset).
    - Never evicts spent grants via TTL/LRU; fails closed on capacity exhaustion or unhealthy state.
    - Process-local QA ledger ONLY; ALL live network session claims fail closed with
      `LIVE_GRANT_REPLAY_DURABILITY_NOT_PROVEN_BLOCKED` until a separately reviewed durable
      cross-process consumption authority is provided in R6+.
    """

    def __init__(
        self,
        *,
        max_entries: int = _MAX_GRANT_LEDGER_ENTRIES_C2,
        durable_cross_process_verified: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._owner_pid = os.getpid()
        self._max_entries = max(1, int(max_entries))
        self._durable_cross_process_verified = bool(durable_cross_process_verified)
        self._healthy = True
        self._unhealthy_reason: Optional[str] = None
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._leases: Dict[str, Tuple[R5GrantSessionLeaseC2, _NonSerializableBindingTokenC1]] = {}

    @property
    def durable_cross_process_verified(self) -> bool:
        return self._durable_cross_process_verified

    def mark_unhealthy_for_qa(self, reason: str) -> None:
        """QA-only fault injector for testing F12 fail-closed ledger unavailability/corruption."""
        with self._lock:
            self._healthy = False
            self._unhealthy_reason = str(reason or "LEDGER_CORRUPT_OR_UNAVAILABLE")

    def _health_error_unlocked(self) -> Optional[str]:
        if not self._healthy:
            return f"LEDGER_UNAVAILABLE_OR_CORRUPT:{self._unhealthy_reason or 'UNHEALTHY'}"
        if self._owner_pid != os.getpid():
            return "CROSS_PROCESS_GRANT_LEDGER_FORBIDDEN"
        return None

    def register_or_check_grant_identity(
        self,
        grant_identity_key: str,
        grant_payload_digest: str,
        *,
        grant_id: str,
        grant_namespace: str,
    ) -> Tuple[bool, List[str]]:
        with self._lock:
            h_err = self._health_error_unlocked()
            if h_err is not None:
                return False, [h_err]
            ent = self._entries.get(grant_identity_key)
            if ent is not None:
                if ent["payload_digest"] != grant_payload_digest:
                    return False, ["GRANT_IDENTITY_PAYLOAD_CONFLICT"]
                return True, []
            if len(self._entries) >= self._max_entries:
                return False, ["GRANT_LEDGER_CAPACITY_EXHAUSTED"]
            self._entries[grant_identity_key] = {
                "grant_identity_key": grant_identity_key,
                "payload_digest": grant_payload_digest,
                "grant_id": str(grant_id),
                "grant_namespace": str(grant_namespace),
                "state": "UNUSED",
                "claimed_session_id": None,
                "claimed_scope_receipt_id": None,
                "claimed_plan_receipt_id": None,
                "claimed_at_utc_ms": None,
                "finalized_outcome": None,
                "max_requests": 0,
                "max_total_bytes": 0,
                "requests_reserved": 0,
                "requests_reserved_total": 0,
                "requests_released_pre_start": 0,
                "requests_started": 0,
                "requests_completed": 0,
                "bytes_reserved": 0,
                "bytes_observed": 0,
                "bytes_uncertain_retained": 0,
                "bytes_accepted": 0,
                "active_workers": 0,
                "pending_workers_at_finalize": 0,
                "attempts": {},
                "replay_denied_count": 0,
            }
            return True, []

    def is_grant_spent_or_claimed(self, grant_identity_key: str) -> Tuple[bool, Optional[str]]:
        with self._lock:
            ent = self._entries.get(grant_identity_key)
            if ent is None:
                return False, None
            st = str(ent["state"])
            if st != "UNUSED":
                return True, st
            return False, st

    def claim_grant_session(
        self,
        *,
        grant_identity_key: str,
        grant_payload_digest: str,
        scope_receipt: R5TrustedScopeReceipt,
        plan_receipt: R5TrustedPlanReceipt,
        session_id: str,
        now_utc: datetime,
        max_requests: int,
        max_total_bytes: int,
        is_live: bool,
    ) -> Tuple[bool, List[str], Optional[R5GrantSessionLeaseC2]]:
        if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
            return False, ["INVALID_OR_NAIVE_UTC_CLOCK"], None
        now_ms = int(now_utc.timestamp() * 1000)
        with self._lock:
            h_err = self._health_error_unlocked()
            if h_err is not None:
                return False, [h_err], None
            if is_live and not self._durable_cross_process_verified:
                return False, ["LIVE_GRANT_REPLAY_DURABILITY_NOT_PROVEN_BLOCKED"], None
            if scope_receipt.expires_utc_ms <= now_ms or plan_receipt.expires_utc_ms <= now_ms:
                return False, ["OPERATOR_GRANT_EXPIRED"], None
            if scope_receipt.grant_identity_key != grant_identity_key or plan_receipt.grant_identity_key != grant_identity_key:
                return False, ["GRANT_IDENTITY_BINDING_MISMATCH"], None

            ent = self._entries.get(grant_identity_key)
            if ent is None:
                if len(self._entries) >= self._max_entries:
                    return False, ["GRANT_LEDGER_CAPACITY_EXHAUSTED"], None
                ent = {
                    "grant_identity_key": grant_identity_key,
                    "payload_digest": grant_payload_digest,
                    "grant_id": scope_receipt.grant_id,
                    "grant_namespace": scope_receipt.grant_namespace,
                    "state": "UNUSED",
                    "claimed_session_id": None,
                    "claimed_scope_receipt_id": None,
                    "claimed_plan_receipt_id": None,
                    "claimed_at_utc_ms": None,
                    "finalized_outcome": None,
                    "max_requests": 0,
                    "max_total_bytes": 0,
                    "requests_reserved": 0,
                    "requests_reserved_total": 0,
                    "requests_released_pre_start": 0,
                    "requests_started": 0,
                    "requests_completed": 0,
                    "bytes_reserved": 0,
                    "bytes_observed": 0,
                    "bytes_uncertain_retained": 0,
                    "bytes_accepted": 0,
                    "active_workers": 0,
                    "pending_workers_at_finalize": 0,
                    "attempts": {},
                    "replay_denied_count": 0,
                }
                self._entries[grant_identity_key] = ent

            if ent["payload_digest"] != grant_payload_digest:
                return False, ["GRANT_IDENTITY_PAYLOAD_CONFLICT"], None
            if ent["state"] != "UNUSED":
                ent["replay_denied_count"] += 1
                return False, [f"GRANT_ALREADY_CLAIMED_OR_CONSUMED:{ent['state']}"], None

            ent["state"] = "CLAIMED"
            ent["claimed_session_id"] = str(session_id)
            ent["claimed_scope_receipt_id"] = scope_receipt.receipt_id
            ent["claimed_plan_receipt_id"] = plan_receipt.plan_receipt_id
            ent["claimed_at_utc_ms"] = now_ms
            ent["max_requests"] = int(max_requests)
            ent["max_total_bytes"] = int(max_total_bytes)

            tok = _NonSerializableBindingTokenC1(secrets.token_hex(12))
            lease_id = f"lse_c2_{canonical_hash({'k': grant_identity_key, 's': session_id, 'p': plan_receipt.plan_receipt_id})[:24]}"
            lease = R5GrantSessionLeaseC2(
                lease_id=lease_id,
                grant_identity_key=grant_identity_key,
                grant_payload_digest=grant_payload_digest,
                grant_id=scope_receipt.grant_id,
                grant_namespace=scope_receipt.grant_namespace,
                session_id=str(session_id),
                scope_receipt_id=scope_receipt.receipt_id,
                plan_receipt_id=plan_receipt.plan_receipt_id,
                max_requests=int(max_requests),
                max_total_bytes=int(max_total_bytes),
                worker_pid=os.getpid(),
                _token=tok,
            )
            self._leases[lease_id] = (lease, tok)
            return True, [], lease

    def _get_active_entry_unlocked(
        self, lease: Any, *, allow_terminal_settlement: bool = False
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        h_err = self._health_error_unlocked()
        if h_err is not None:
            return None, h_err
        if not isinstance(lease, R5GrantSessionLeaseC2):
            return None, "INVALID_GRANT_SESSION_LEASE"
        reg = self._leases.get(lease.lease_id)
        if reg is None or reg[0] is not lease or reg[1] is not lease._token:
            return None, "UNREGISTERED_OR_TAMPERED_GRANT_LEASE"
        if int(lease.worker_pid) != os.getpid():
            return None, "CROSS_PROCESS_GRANT_LEASE_FORBIDDEN"
        ent = self._entries.get(lease.grant_identity_key)
        if ent is None or ent["claimed_session_id"] != lease.session_id:
            return None, "GRANT_LEASE_SESSION_MISMATCH"
        if (not allow_terminal_settlement) and ent["state"] != "CLAIMED":
            return None, f"GRANT_SESSION_NOT_ACTIVE:{ent['state']}"
        return ent, None

    def reserve_dispatch(
        self,
        lease: R5GrantSessionLeaseC2,
        attempt_id: str,
        *,
        max_response_bytes: int,
    ) -> Tuple[bool, Optional[str], int, bool]:
        """
        Atomically reserves 1 request slot + dynamic `call_byte_cap <= min(max_response_bytes, rem_bytes)`.
        Returns `(ok, reason, call_byte_cap, should_wait_for_inflight_bytes)`.
        """
        with self._lock:
            ent, err = self._get_active_entry_unlocked(lease)
            if err is not None or ent is None:
                return False, err or "INVALID_LEASE", 0, False
            aid = str(attempt_id).strip()
            if not aid or aid in ent["attempts"]:
                return False, f"DUPLICATE_OR_EMPTY_ATTEMPT_ID:{aid!r}", 0, False
            if ent["requests_started"] + ent["requests_reserved"] >= ent["max_requests"]:
                return False, "MAX_REQUESTS_BUDGET_EXHAUSTED", 0, False

            charged_bytes = ent["bytes_observed"] + ent["bytes_uncertain_retained"]
            if charged_bytes >= ent["max_total_bytes"]:
                return False, "TOTAL_BYTES_BUDGET_EXHAUSTED", 0, False
            rem_unreserved = ent["max_total_bytes"] - charged_bytes - ent["bytes_reserved"]
            if rem_unreserved <= 0:
                if ent["bytes_reserved"] > 0:
                    return False, "WAIT_INFLIGHT_BYTE_RESERVATIONS", 0, True
                return False, "TOTAL_BYTES_BUDGET_EXHAUSTED", 0, False

            call_cap = min(int(max_response_bytes), int(rem_unreserved))
            if call_cap <= 0:
                return False, "TOTAL_BYTES_BUDGET_EXHAUSTED", 0, False

            ent["requests_reserved"] += 1
            ent["requests_reserved_total"] += 1
            ent["bytes_reserved"] += call_cap
            ent["attempts"][aid] = {
                "state": "RESERVED",
                "byte_cap": call_cap,
                "observed_bytes": None,
                "bytes_known": False,
                "uncertain_charged": 0,
            }
            return True, None, call_cap, False

    def mark_dispatch_started(self, lease: R5GrantSessionLeaseC2, attempt_id: str) -> Tuple[bool, Optional[str]]:
        with self._lock:
            ent, err = self._get_active_entry_unlocked(lease)
            if err is not None or ent is None:
                return False, err or "INVALID_LEASE"
            att = ent["attempts"].get(str(attempt_id))
            if att is None:
                return False, f"UNKNOWN_ATTEMPT_ID:{attempt_id}"
            if att["state"] != "RESERVED":
                return False, f"ATTEMPT_ALREADY_TRANSITIONED:{att['state']}"
            if ent["requests_reserved"] <= 0:
                return False, "LEDGER_UNDERFLOW_GUARD"
            att["state"] = "STARTED"
            ent["requests_reserved"] -= 1
            ent["requests_started"] += 1
            ent["active_workers"] += 1
            return True, None

    def release_unstarted_reservation(self, lease: R5GrantSessionLeaseC2, attempt_id: str) -> Tuple[bool, Optional[str]]:
        with self._lock:
            ent, err = self._get_active_entry_unlocked(lease, allow_terminal_settlement=True)
            if err is not None or ent is None:
                return False, err or "INVALID_LEASE"
            att = ent["attempts"].get(str(attempt_id))
            if att is None:
                return False, f"UNKNOWN_ATTEMPT_ID:{attempt_id}"
            if att["state"] != "RESERVED":
                return False, f"CANNOT_RELEASE_NON_RESERVED_ATTEMPT:{att['state']}"
            cap = int(att["byte_cap"])
            if ent["requests_reserved"] <= 0 or ent["bytes_reserved"] < cap:
                return False, "LEDGER_OVER_RELEASE_BLOCKED"
            att["state"] = "RELEASED_PRE_START"
            ent["requests_reserved"] -= 1
            ent["requests_released_pre_start"] += 1
            ent["bytes_reserved"] -= cap
            return True, None

    def settle_dispatch(
        self,
        lease: R5GrantSessionLeaseC2,
        attempt_id: str,
        *,
        observed_bytes: Optional[int],
        bytes_known: bool,
        accepted_bytes: int = 0,
    ) -> Tuple[bool, Optional[str], bool]:
        """
        Settles a `STARTED` attempt exactly once. When `bytes_known=False` (timeout/transport error/
        partial unknown read), retains conservative charge at `byte_cap` (`bytes_uncertain_retained`),
        NEVER crediting unknown wire bytes as zero! Returns `(ok, err, over_total_byte_budget)`.
        """
        with self._lock:
            ent, err = self._get_active_entry_unlocked(lease, allow_terminal_settlement=True)
            if err is not None or ent is None:
                return False, err or "INVALID_LEASE", False
            att = ent["attempts"].get(str(attempt_id))
            if att is None:
                return False, f"UNKNOWN_ATTEMPT_ID:{attempt_id}", False
            if att["state"] != "STARTED":
                return False, f"ATTEMPT_NOT_STARTED_OR_ALREADY_SETTLED:{att['state']}", False
            cap = int(att["byte_cap"])
            if ent["bytes_reserved"] < cap or ent["active_workers"] <= 0:
                return False, "LEDGER_OVER_SETTLE_OR_UNDERFLOW_BLOCKED", False

            ent["bytes_reserved"] -= cap
            ent["active_workers"] -= 1
            ent["requests_completed"] += 1
            att["state"] = "SETTLED"
            att["bytes_known"] = bool(bytes_known)

            if bytes_known and observed_bytes is not None and not isinstance(observed_bytes, bool) and int(observed_bytes) >= 0:
                obs = int(observed_bytes)
                att["observed_bytes"] = obs
                ent["bytes_observed"] += obs
            else:
                att["observed_bytes"] = None
                att["uncertain_charged"] = cap
                ent["bytes_uncertain_retained"] += cap

            if accepted_bytes > 0:
                ent["bytes_accepted"] += int(accepted_bytes)

            total_charged = ent["bytes_observed"] + ent["bytes_uncertain_retained"]
            over_budget = total_charged > ent["max_total_bytes"]
            return True, None, over_budget

    def finalize_grant_session(
        self,
        lease: R5GrantSessionLeaseC2,
        outcome: str,
        *,
        pending_workers: int = 0,
        abort_discard_accepted: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        with self._lock:
            ent, err = self._get_active_entry_unlocked(lease, allow_terminal_settlement=True)
            if err is not None or ent is None:
                return False, err or "INVALID_LEASE", None
            if ent["state"] in ("CONSUMED", "ABORTED", "QUARANTINED"):
                return True, "ALREADY_FINALIZED", self._snapshot_entry_unlocked(ent)

            if abort_discard_accepted:
                ent["bytes_accepted"] = 0
            ent["pending_workers_at_finalize"] = max(0, int(pending_workers))
            if pending_workers > 0 or outcome == "QUARANTINED":
                ent["state"] = "QUARANTINED"
            elif outcome == "ABORTED":
                ent["state"] = "ABORTED"
            else:
                ent["state"] = "CONSUMED"
            ent["finalized_outcome"] = str(outcome)
            return True, None, self._snapshot_entry_unlocked(ent)

    def _snapshot_entry_unlocked(self, ent: Mapping[str, Any]) -> Dict[str, Any]:
        tot_charged = int(ent["bytes_observed"]) + int(ent["bytes_uncertain_retained"])
        no_neg = all(
            int(ent[k]) >= 0
            for k in (
                "requests_reserved", "requests_reserved_total", "requests_released_pre_start",
                "requests_started", "requests_completed", "bytes_reserved", "bytes_observed",
                "bytes_uncertain_retained", "bytes_accepted", "active_workers",
            )
        )
        req_conserved = (
            int(ent["requests_reserved_total"])
            == int(ent["requests_started"]) + int(ent["requests_released_pre_start"]) + int(ent["requests_reserved"])
        )
        return {
            "grant_identity_key": ent["grant_identity_key"],
            "grant_payload_digest": ent["payload_digest"],
            "grant_id": ent["grant_id"],
            "grant_namespace": ent["grant_namespace"],
            "state": ent["state"],
            "claimed_session_id": ent["claimed_session_id"],
            "finalized_outcome": ent["finalized_outcome"],
            "max_requests": ent["max_requests"],
            "max_total_bytes": ent["max_total_bytes"],
            "requests_reserved": ent["requests_reserved"],
            "requests_reserved_total": ent["requests_reserved_total"],
            "requests_released_pre_start": ent["requests_released_pre_start"],
            "requests_started": ent["requests_started"],
            "requests_completed": ent["requests_completed"],
            "bytes_reserved": ent["bytes_reserved"],
            "bytes_observed": ent["bytes_observed"],
            "bytes_uncertain_retained": ent["bytes_uncertain_retained"],
            "bytes_total_charged": tot_charged,
            "bytes_accepted": ent["bytes_accepted"],
            "active_workers": ent["active_workers"],
            "pending_workers_at_finalize": ent["pending_workers_at_finalize"],
            "replay_denied_count": ent["replay_denied_count"],
            "durable_cross_process_verified": self._durable_cross_process_verified,
            "counters_non_negative": no_neg,
            "request_conservation_exact": req_conserved,
        }

    def snapshot_grant(self, grant_identity_key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            ent = self._entries.get(grant_identity_key)
            if ent is None:
                return None
            return self._snapshot_entry_unlocked(ent)


def _compute_c1_mac(fields: Mapping[str, Any]) -> str:
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_SCOPE_BINDING_KEY_C1, raw, hashlib.sha256).hexdigest()


class _ScopeBindingAuthorityC1:
    """
    Process-local registry binding `validate_r5_operator_capture_scope` -> `plan_r5_capture_batches`
    -> `capture_r5_public_batch`. Defeats forged dicts, missing receipts, plan mutations before or
    after enqueue, and recomputed-public-digest tampering within the documented threat model.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scopes: Dict[str, Tuple[R5TrustedScopeReceipt, _NonSerializableBindingTokenC1]] = {}
        self._scope_order: List[str] = []
        self._plans: Dict[str, Tuple[R5TrustedPlanReceipt, _NonSerializableBindingTokenC1]] = {}
        self._plan_order: List[str] = []
        self.ledger = R5GrantConsumptionLedgerC2(durable_cross_process_verified=False)

    def _evict(self, store: Dict[str, Any], order: List[str]) -> None:
        while len(order) >= _MAX_BINDING_REGISTRY_C1:
            oldest = order.pop(0)
            store.pop(oldest, None)

    def issue_scope(
        self,
        *,
        mode: str,
        authorization_status: str,
        live_network_permitted: bool,
        grant_id: str,
        issued_by: str,
        purpose: str,
        run_id: str,
        expires_utc_ms: int,
        effective_scope: Mapping[str, Any],
        scope_digest_sha256: str,
        verifier_id: str,
        grant_identity_key: str = "",
        grant_payload_digest: str = "",
        grant_namespace: str = "QA_FIXTURE",
    ) -> R5TrustedScopeReceipt:
        nonce = secrets.token_hex(12)
        rid = f"scp_c1_{canonical_hash({'n': nonce, 'd': scope_digest_sha256, 'r': run_id})[:24]}"
        eff_copy = json.loads(json.dumps(dict(effective_scope), sort_keys=True))
        fields = {
            "receipt_id": rid,
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "mode": mode,
            "authorization_status": authorization_status,
            "live_network_permitted": bool(live_network_permitted),
            "grant_id": grant_id,
            "issued_by": issued_by,
            "purpose": purpose,
            "run_id": run_id,
            "expires_utc_ms": int(expires_utc_ms),
            "effective_scope": eff_copy,
            "scope_digest_sha256": scope_digest_sha256,
            "verifier_id": verifier_id,
            "worker_pid": os.getpid(),
            "grant_identity_key": grant_identity_key,
            "grant_payload_digest": grant_payload_digest,
            "grant_namespace": grant_namespace,
        }
        mac = _compute_c1_mac(fields)
        tok = _NonSerializableBindingTokenC1(nonce)
        rec = R5TrustedScopeReceipt(**fields, receipt_mac_hex=mac, _token=tok)
        with self._lock:
            self._evict(self._scopes, self._scope_order)
            self._scopes[rid] = (rec, tok)
            self._scope_order.append(rid)
        return rec

    def verify_scope(
        self,
        receipt: Any,
        *,
        now_utc: Optional[datetime] = None,
        expected_run_id: Optional[str] = None,
        expected_scope_digest: Optional[str] = None,
    ) -> Tuple[bool, List[str], Optional[R5TrustedScopeReceipt]]:
        viols: List[str] = []
        if not isinstance(receipt, R5TrustedScopeReceipt):
            return False, ["MISSING_OR_INVALID_TRUSTED_SCOPE_RECEIPT"], None
        with self._lock:
            reg = self._scopes.get(receipt.receipt_id)
        if reg is None:
            return False, ["UNREGISTERED_OR_FORGED_TRUSTED_SCOPE_RECEIPT"], None
        reg_rec, reg_tok = reg
        if receipt is not reg_rec or receipt._token is not reg_tok:
            return False, ["CLONED_OR_TAMPERED_SCOPE_RECEIPT_TOKEN"], None
        if int(receipt.worker_pid) != os.getpid() or _SCOPE_BINDING_PID_C1 != os.getpid():
            viols.append("CROSS_PROCESS_SCOPE_RECEIPT_FORBIDDEN")
        fields = {
            "receipt_id": receipt.receipt_id,
            "spec_version": receipt.spec_version,
            "mode": receipt.mode,
            "authorization_status": receipt.authorization_status,
            "live_network_permitted": bool(receipt.live_network_permitted),
            "grant_id": receipt.grant_id,
            "issued_by": receipt.issued_by,
            "purpose": receipt.purpose,
            "run_id": receipt.run_id,
            "expires_utc_ms": int(receipt.expires_utc_ms),
            "effective_scope": receipt.effective_scope,
            "scope_digest_sha256": receipt.scope_digest_sha256,
            "verifier_id": receipt.verifier_id,
            "worker_pid": int(receipt.worker_pid),
            "grant_identity_key": receipt.grant_identity_key,
            "grant_payload_digest": receipt.grant_payload_digest,
            "grant_namespace": receipt.grant_namespace,
        }
        if not hmac.compare_digest(receipt.receipt_mac_hex, _compute_c1_mac(fields)):
            viols.append("SCOPE_RECEIPT_MAC_TAMPERED")
        if expected_scope_digest is not None and receipt.scope_digest_sha256 != str(expected_scope_digest):
            viols.append("SCOPE_DIGEST_MISMATCH_VS_TRUSTED_RECEIPT")
        if expected_run_id is not None and receipt.run_id != str(expected_run_id):
            viols.append("RUN_ID_SCOPE_MISMATCH")
        if now_utc is not None:
            if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
                viols.append("INVALID_OR_NAIVE_UTC_CLOCK")
            else:
                now_ms = int(now_utc.timestamp() * 1000)
                if receipt.expires_utc_ms <= now_ms:
                    viols.append("OPERATOR_GRANT_EXPIRED")
        return len(viols) == 0, viols, receipt

    def issue_plan(
        self,
        *,
        scope_receipt: R5TrustedScopeReceipt,
        plan_public_snapshot: Mapping[str, Any],
        plan_digest_sha256: str,
    ) -> R5TrustedPlanReceipt:
        nonce = secrets.token_hex(12)
        prid = f"pln_c1_{canonical_hash({'n': nonce, 'p': plan_digest_sha256, 's': scope_receipt.receipt_id})[:24]}"
        snap_json = json.dumps(plan_public_snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fields = {
            "plan_receipt_id": prid,
            "scope_receipt_id": scope_receipt.receipt_id,
            "scope_digest_sha256": scope_receipt.scope_digest_sha256,
            "plan_digest_sha256": plan_digest_sha256,
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "partition_role": str(plan_public_snapshot["partition_role"]),
            "live_network_permitted": bool(plan_public_snapshot["live_network_permitted"]),
            "grant_id": scope_receipt.grant_id,
            "run_id": scope_receipt.run_id,
            "expires_utc_ms": int(scope_receipt.expires_utc_ms),
            "destination_scope": scope_receipt.effective_scope.get("destination_scope"),
            "immutable_plan_snapshot_json": snap_json,
            "worker_pid": os.getpid(),
            "grant_identity_key": scope_receipt.grant_identity_key,
            "grant_payload_digest": scope_receipt.grant_payload_digest,
            "grant_namespace": scope_receipt.grant_namespace,
        }
        mac = _compute_c1_mac(fields)
        tok = _NonSerializableBindingTokenC1(nonce)
        rec = R5TrustedPlanReceipt(**fields, receipt_mac_hex=mac, _token=tok)
        with self._lock:
            self._evict(self._plans, self._plan_order)
            self._plans[prid] = (rec, tok)
            self._plan_order.append(prid)
        return rec

    def verify_plan_receipt(
        self,
        plan_receipt: Any,
        *,
        scope_receipt: Optional[R5TrustedScopeReceipt] = None,
        now_utc: Optional[datetime] = None,
        expected_run_id: Optional[str] = None,
    ) -> Tuple[bool, List[str], Optional[R5TrustedPlanReceipt], Optional[R5TrustedScopeReceipt]]:
        viols: List[str] = []
        if not isinstance(plan_receipt, R5TrustedPlanReceipt):
            return False, ["MISSING_OR_INVALID_TRUSTED_PLAN_RECEIPT"], None, None
        with self._lock:
            reg = self._plans.get(plan_receipt.plan_receipt_id)
            sreg = self._scopes.get(plan_receipt.scope_receipt_id)
        if reg is None:
            return False, ["UNREGISTERED_OR_FORGED_TRUSTED_PLAN_RECEIPT"], None, None
        reg_rec, reg_tok = reg
        if plan_receipt is not reg_rec or plan_receipt._token is not reg_tok:
            return False, ["CLONED_OR_TAMPERED_PLAN_RECEIPT_TOKEN"], None, None
        if int(plan_receipt.worker_pid) != os.getpid() or _SCOPE_BINDING_PID_C1 != os.getpid():
            viols.append("CROSS_PROCESS_PLAN_RECEIPT_FORBIDDEN")
        fields = {
            "plan_receipt_id": plan_receipt.plan_receipt_id,
            "scope_receipt_id": plan_receipt.scope_receipt_id,
            "scope_digest_sha256": plan_receipt.scope_digest_sha256,
            "plan_digest_sha256": plan_receipt.plan_digest_sha256,
            "spec_version": plan_receipt.spec_version,
            "partition_role": plan_receipt.partition_role,
            "live_network_permitted": bool(plan_receipt.live_network_permitted),
            "grant_id": plan_receipt.grant_id,
            "run_id": plan_receipt.run_id,
            "expires_utc_ms": int(plan_receipt.expires_utc_ms),
            "destination_scope": plan_receipt.destination_scope,
            "immutable_plan_snapshot_json": plan_receipt.immutable_plan_snapshot_json,
            "worker_pid": int(plan_receipt.worker_pid),
            "grant_identity_key": plan_receipt.grant_identity_key,
            "grant_payload_digest": plan_receipt.grant_payload_digest,
            "grant_namespace": plan_receipt.grant_namespace,
        }
        if not hmac.compare_digest(plan_receipt.receipt_mac_hex, _compute_c1_mac(fields)):
            viols.append("PLAN_RECEIPT_MAC_TAMPERED")
        if sreg is None:
            return False, viols + ["PLAN_BOUND_SCOPE_RECEIPT_MISSING"], None, None
        bound_scope = sreg[0]
        if scope_receipt is not None:
            if not isinstance(scope_receipt, R5TrustedScopeReceipt) or scope_receipt is not bound_scope:
                viols.append("PLAN_SCOPE_RECEIPT_BINDING_MISMATCH")
        if plan_receipt.grant_identity_key != bound_scope.grant_identity_key or plan_receipt.grant_payload_digest != bound_scope.grant_payload_digest:
            viols.append("PLAN_GRANT_IDENTITY_MISMATCH")
        s_ok, s_viols, _ = self.verify_scope(
            bound_scope, now_utc=now_utc, expected_run_id=expected_run_id, expected_scope_digest=plan_receipt.scope_digest_sha256
        )
        if not s_ok:
            viols.extend(s_viols)
        if expected_run_id is not None and plan_receipt.run_id != str(expected_run_id):
            viols.append("PLAN_RUN_ID_MISMATCH")
        return len(viols) == 0, viols, plan_receipt, bound_scope


_GLOBAL_SCOPE_AUTHORITY_C1 = _ScopeBindingAuthorityC1()


def get_r5_global_grant_ledger_c2() -> R5GrantConsumptionLedgerC2:
    """Returns the process-local KH-03.R5.C2 one-shot grant consumption ledger."""
    return _GLOBAL_SCOPE_AUTHORITY_C1.ledger


def _strict_pos_int_c1(val: Any, field: str, *, max_val: Optional[int] = None) -> Tuple[Optional[int], Optional[str]]:
    if isinstance(val, bool) or not isinstance(val, int):
        return None, f"INVALID_INT_TYPE:{field}:{type(val).__name__}"
    if val <= 0:
        return None, f"NON_POSITIVE_INT:{field}:{val}"
    if max_val is not None and val > max_val:
        return None, f"INT_EXCEEDS_HARD_CEILING:{field}:{val}>{max_val}"
    return int(val), None


def _strict_nonneg_int_c1(val: Any, field: str, *, max_val: Optional[int] = None) -> Tuple[Optional[int], Optional[str]]:
    if isinstance(val, bool) or not isinstance(val, int):
        return None, f"INVALID_INT_TYPE:{field}:{type(val).__name__}"
    if val < 0:
        return None, f"NEGATIVE_INT:{field}:{val}"
    if max_val is not None and val > max_val:
        return None, f"INT_EXCEEDS_HARD_CEILING:{field}:{val}>{max_val}"
    return int(val), None


def _strict_finite_float_c1(val: Any, field: str) -> Tuple[Optional[float], Optional[str]]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None, f"INVALID_FLOAT_TYPE:{field}:{type(val).__name__}"
    fv = float(val)
    if not math.isfinite(fv):
        return None, f"NONFINITE_FLOAT:{field}:{val!r}"
    return fv, None


def _strict_canonical_id_c1(val: Any, field: str) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(val, str):
        return None, f"INVALID_STR_TYPE:{field}:{type(val).__name__}"
    s = val.strip()
    if not s or s != val or any(ch.isspace() for ch in s):
        return None, f"MALFORMED_CANONICAL_ID:{field}:{val!r}"
    return s, None


def _contains_forbidden_oos_token_c1(text: str) -> bool:
    low = str(text or "").lower().replace("\\", "/")
    return any(tok in low for tok in FORBIDDEN_OOS_SCOPE_TOKENS_R5_C1)


def _normalize_r5_capture_scope_strict(requested_scope: Any) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Strictly validates and normalizes `requested_scope`.
    Fail-closed on non-mappings, secrets, bool-as-int, NaN/Inf, missing mandatory fields,
    duplicate/unsupported endpoints, invalid/inverted UTC ms window, or OOS purpose/destination.
    """
    if not isinstance(requested_scope, Mapping):
        return False, ["REQUESTED_SCOPE_NOT_MAPPING"], None
    raw_dict = dict(requested_scope)
    if scan_for_forbidden_secrets_r2_c1(raw_dict):
        return False, ["FORBIDDEN_SECRET_IN_CAPTURE_SCOPE_OR_CONTEXT"], None

    viols: List[str] = []
    sym, e = _strict_canonical_id_c1(raw_dict.get("symbol"), "symbol")
    if e:
        viols.append(e)
    elif sym != sym.upper():
        viols.append(f"NON_CANONICAL_SYMBOL:{sym!r}")

    pt, e = _strict_canonical_id_c1(raw_dict.get("product_type"), "product_type")
    if e:
        viols.append(e)
    elif pt not in ALLOWED_PRODUCT_TYPES_R5_C1:
        viols.append(f"UNSUPPORTED_PRODUCT_TYPE:{pt!r}")

    rid, e = _strict_canonical_id_c1(raw_dict.get("run_id"), "run_id")
    if e:
        viols.append(e)
    elif _contains_forbidden_oos_token_c1(rid or ""):
        viols.append("OOS_TOKEN_FORBIDDEN_IN_RUN_ID")

    purp, e = _strict_canonical_id_c1(raw_dict.get("purpose"), "purpose")
    if e:
        viols.append(e)
    else:
        if _contains_forbidden_oos_token_c1(purp or ""):
            viols.append("OOS_CAPTURE_OR_EVALUATION_FORBIDDEN_IN_R5")
        elif purp not in ALLOWED_CAPTURE_PURPOSES_R5_C1:
            viols.append(f"FORBIDDEN_CAPTURE_PURPOSE:{purp!r}")

    eps_raw = raw_dict.get("endpoints")
    norm_eps: List[str] = []
    if not isinstance(eps_raw, (list, tuple)) or len(eps_raw) == 0:
        viols.append("MISSING_OR_EMPTY_ENDPOINTS")
    else:
        seen_ep: set[str] = set()
        for ep in eps_raw:
            ep_s, ep_e = _strict_canonical_id_c1(ep, "endpoint")
            if ep_e:
                viols.append(ep_e)
                continue
            assert ep_s is not None
            if ep_s in seen_ep:
                viols.append(f"DUPLICATE_ENDPOINT_IN_SCOPE:{ep_s}")
                continue
            seen_ep.add(ep_s)
            if ep_s not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
                viols.append(f"REQUESTED_ENDPOINT_NOT_IN_PUBLIC_GET_ALLOWLIST:{ep_s}")
                continue
            url_ep = str(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2[ep_s]["url"])
            path_part = url_ep.split(OFFICIAL_BITGET_PUBLIC_HOST_C3, 1)[-1]
            if path_part not in OFFICIAL_BITGET_PUBLIC_PATHS_C3 or any(t in url_ep.lower() for t in FORBIDDEN_URL_TOKENS_C2):
                viols.append(f"ENDPOINT_OUTSIDE_C3_TRUST_BOUNDARY:{ep_s}")
                continue
            norm_eps.append(ep_s)

    ws: Optional[int] = None
    we: Optional[int] = None
    for k_name in ("window_start_ms", "window_end_ms"):
        if raw_dict.get(k_name) is None:
            viols.append(f"MISSING_REQUIRED_FIELD:{k_name}")
        elif not _is_strict_int(raw_dict.get(k_name)):
            viols.append(f"INVALID_INT_TYPE:{k_name}:{type(raw_dict.get(k_name)).__name__}")
        else:
            try:
                v_ms = _to_utc_ms(raw_dict[k_name], field=k_name)
                if k_name == "window_start_ms":
                    ws = v_ms
                else:
                    we = v_ms
            except ValueError as exc:
                viols.append(str(exc))
    if ws is not None and we is not None and we <= ws:
        viols.append("INVALID_CAPTURE_TIME_WINDOW")

    req_max_req, e = _strict_pos_int_c1(raw_dict.get("max_requests"), "max_requests", max_val=HARD_MAX_REQUESTS_R5_C1)
    if e:
        viols.append(e)
    req_max_bytes, e = _strict_pos_int_c1(raw_dict.get("max_total_bytes"), "max_total_bytes", max_val=HARD_MAX_TOTAL_BYTES_R5_C1)
    if e:
        viols.append(e)
    req_max_dur, e = _strict_pos_int_c1(raw_dict.get("max_duration_sec"), "max_duration_sec", max_val=HARD_MAX_DURATION_SEC_R5_C1)
    if e:
        viols.append(e)

    # Optional explicit bounded fields (strict type check when supplied)
    opt_vals: Dict[str, Optional[int]] = {}
    for fk, max_c, allow_zero in (
        ("max_pages", HARD_MAX_PAGES_R5_C1, False),
        ("max_response_bytes", DEFAULT_MAX_PAGE_BYTES_R5, False),
        ("max_retries_per_request", HARD_MAX_RETRIES_R5_C1, True),
        ("max_concurrency", MAX_GLOBAL_CONCURRENCY_R5, False),
        ("min_request_interval_ms", 60_000, False),
    ):
        if fk in raw_dict and raw_dict[fk] is not None:
            v_i, err_i = _strict_nonneg_int_c1(raw_dict[fk], fk, max_val=max_c) if allow_zero else _strict_pos_int_c1(raw_dict[fk], fk, max_val=max_c)
            if err_i:
                viols.append(err_i)
            elif fk == "min_request_interval_ms" and v_i is not None and v_i < HARD_MIN_REQUEST_INTERVAL_MS_R5_C1:
                viols.append(f"MIN_REQUEST_INTERVAL_BELOW_FLOOR:{v_i}<{HARD_MIN_REQUEST_INTERVAL_MS_R5_C1}")
            opt_vals[fk] = v_i
        else:
            opt_vals[fk] = None

    dest_scope = raw_dict.get("destination_scope")
    if dest_scope is not None:
        if not isinstance(dest_scope, str) or not dest_scope.strip():
            viols.append("INVALID_DESTINATION_SCOPE")
        elif _contains_forbidden_oos_token_c1(dest_scope):
            viols.append("OOS_DESTINATION_SCOPE_FORBIDDEN")
        else:
            dest_scope = dest_scope.strip()

    if raw_dict.get("grant_id") is not None:
        gid_s, gid_e = _strict_canonical_id_c1(raw_dict.get("grant_id"), "grant_id")
        if gid_e:
            viols.append(gid_e)
        req_grant_id = gid_s
    else:
        req_grant_id = None

    if viols:
        return False, viols, None

    return True, [], {
        "symbol": sym,
        "product_type": pt,
        "endpoints": tuple(norm_eps),
        "run_id": rid,
        "purpose": purp,
        "window_start_ms": ws,
        "window_end_ms": we,
        "max_requests": req_max_req,
        "max_total_bytes": req_max_bytes,
        "max_duration_sec": req_max_dur,
        "max_pages": opt_vals["max_pages"],
        "max_response_bytes": opt_vals["max_response_bytes"],
        "max_retries_per_request": opt_vals["max_retries_per_request"],
        "max_concurrency": opt_vals["max_concurrency"],
        "min_request_interval_ms": opt_vals["min_request_interval_ms"],
        "destination_scope": dest_scope,
        "grant_id": req_grant_id,
    }


def _validate_r5_budget_within_scope(
    budget: Any,
    effective_scope: Mapping[str, Any],
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Validates planner `budget` strictly against approved `effective_scope` and hard local caps.
    NEVER silently clamps an unauthorized larger request/endpoint/window/symbol — fails closed!
    Permits exact match or explicit strictly smaller authorized subset.
    """
    if not isinstance(budget, Mapping):
        return False, ["BUDGET_NOT_MAPPING"], None
    b = dict(budget)
    if scan_for_forbidden_secrets_r2_c1(b):
        return False, ["FORBIDDEN_SECRET_IN_BUDGET"], None

    viols: List[str] = []
    if b.get("owner_waive_disk_preflight") is True:
        viols.append("DISK_WAIVER_STRICTLY_FORBIDDEN_IN_R5")
    elif "owner_waive_disk_preflight" in b and not isinstance(b["owner_waive_disk_preflight"], bool):
        viols.append("INVALID_DISK_WAIVER_FLAG_TYPE")

    disk_free, e = _strict_finite_float_c1(b.get("destination_disk_free_gib"), "destination_disk_free_gib")
    if e:
        viols.append(e)
    elif disk_free is not None and disk_free < STRICT_PREFLIGHT_DISK_MIN_GIB_R5:
        viols.append(f"BLOCKED_LOW_DISK_PREFLIGHT:{disk_free:.4f}<{STRICT_PREFLIGHT_DISK_MIN_GIB_R5}")

    # Scope identity fields: if supplied in budget, MUST match effective_scope!
    for fk in ("symbol", "product_type", "run_id", "purpose", "grant_id"):
        if fk in b and b[fk] is not None:
            if str(b[fk]) != str(effective_scope.get(fk)):
                viols.append(f"PLAN_BUDGET_SCOPE_MISMATCH:{fk}:got={b[fk]!r}!=approved={effective_scope.get(fk)!r}")
    if "destination_scope" in b and b["destination_scope"] is not None:
        if _contains_forbidden_oos_token_c1(str(b["destination_scope"])):
            viols.append("OOS_DESTINATION_SCOPE_FORBIDDEN")
        if effective_scope.get("destination_scope") is not None and str(b["destination_scope"]).strip() != str(effective_scope["destination_scope"]).strip():
            viols.append("PLAN_BUDGET_DESTINATION_SCOPE_MISMATCH")

    # Endpoints: default to effective_scope["endpoints"]; if provided, MUST be non-empty unique subset!
    approved_eps = list(effective_scope["endpoints"])
    if "endpoints" in b and b["endpoints"] is not None:
        if not isinstance(b["endpoints"], (list, tuple)) or len(b["endpoints"]) == 0:
            viols.append("INVALID_OR_EMPTY_PLAN_ENDPOINTS")
            plan_eps: List[str] = []
        else:
            seen: set[str] = set()
            plan_eps = []
            for ep in b["endpoints"]:
                ep_s, ep_e = _strict_canonical_id_c1(ep, "endpoint")
                if ep_e:
                    viols.append(ep_e)
                    continue
                assert ep_s is not None
                if ep_s in seen:
                    viols.append(f"DUPLICATE_ENDPOINT_IN_PLAN_BUDGET:{ep_s}")
                    continue
                seen.add(ep_s)
                if ep_s not in approved_eps:
                    viols.append(f"PLAN_ENDPOINT_OUTSIDE_APPROVED_SCOPE:{ep_s}")
                    continue
                plan_eps.append(ep_s)
    else:
        plan_eps = list(approved_eps)

    # Window: default to approved effective window; if provided, MUST lie strictly within approved window!
    eff_ws = int(effective_scope["window_start_ms"])
    eff_we = int(effective_scope["window_end_ms"])
    plan_ws = eff_ws
    plan_we = eff_we
    if "window_start_ms" in b and b["window_start_ms"] is not None:
        if not _is_strict_int(b["window_start_ms"]):
            viols.append("INVALID_INT_TYPE:window_start_ms")
        else:
            try:
                plan_ws = _to_utc_ms(b["window_start_ms"], field="window_start_ms")
                if plan_ws < eff_ws:
                    viols.append(f"PLAN_WINDOW_START_BEFORE_APPROVED_SCOPE:{plan_ws}<{eff_ws}")
            except ValueError as exc:
                viols.append(str(exc))
    if "window_end_ms" in b and b["window_end_ms"] is not None:
        if not _is_strict_int(b["window_end_ms"]):
            viols.append("INVALID_INT_TYPE:window_end_ms")
        else:
            try:
                plan_we = _to_utc_ms(b["window_end_ms"], field="window_end_ms")
                if plan_we > eff_we:
                    viols.append(f"PLAN_WINDOW_END_AFTER_APPROVED_SCOPE:{plan_we}>{eff_we}")
            except ValueError as exc:
                viols.append(str(exc))
    if plan_we <= plan_ws:
        viols.append("INVALID_PLAN_TIME_WINDOW")

    # Numeric budget bounds: strict positive ints, NEVER exceeding effective_scope or local ceilings!
    def _bound_pos(key: str, eff_cap: int, hard_cap: int, default_val: Optional[int] = None) -> Optional[int]:
        raw_v = b.get(key, default_val)
        v_i, err_i = _strict_pos_int_c1(raw_v, key, max_val=hard_cap)
        if err_i:
            viols.append(err_i)
            return None
        assert v_i is not None
        if v_i > eff_cap:
            viols.append(f"PLAN_BUDGET_EXCEEDS_APPROVED_SCOPE:{key}:{v_i}>{eff_cap}")
            return None
        return v_i

    max_req = _bound_pos("max_requests", int(effective_scope["max_requests"]), HARD_MAX_REQUESTS_R5_C1)
    max_bytes = _bound_pos("max_total_bytes", int(effective_scope["max_total_bytes"]), HARD_MAX_TOTAL_BYTES_R5_C1)
    max_dur = _bound_pos("max_duration_sec", int(effective_scope["max_duration_sec"]), HARD_MAX_DURATION_SEC_R5_C1)
    max_pages = _bound_pos("max_pages", int(effective_scope["max_pages"]), HARD_MAX_PAGES_R5_C1)
    max_conc = _bound_pos("max_concurrency", int(effective_scope["max_concurrency"]), MAX_GLOBAL_CONCURRENCY_R5)
    max_resp = _bound_pos(
        "max_response_bytes",
        int(effective_scope["max_response_bytes"]),
        DEFAULT_MAX_PAGE_BYTES_R5,
        default_val=int(effective_scope["max_response_bytes"]),
    )

    raw_ret = b.get("max_retries_per_request", int(effective_scope["max_retries_per_request"]))
    max_ret, err_ret = _strict_nonneg_int_c1(raw_ret, "max_retries_per_request", max_val=HARD_MAX_RETRIES_R5_C1)
    if err_ret:
        viols.append(err_ret)
    elif max_ret is not None and max_ret > int(effective_scope["max_retries_per_request"]):
        viols.append(f"PLAN_BUDGET_EXCEEDS_APPROVED_SCOPE:max_retries_per_request:{max_ret}>{effective_scope['max_retries_per_request']}")

    raw_iv = b.get("min_request_interval_ms", int(effective_scope["min_request_interval_ms"]))
    min_iv, err_iv = _strict_pos_int_c1(raw_iv, "min_request_interval_ms", max_val=60_000)
    if err_iv:
        viols.append(err_iv)
    elif min_iv is not None and min_iv < int(effective_scope["min_request_interval_ms"]):
        viols.append(f"PLAN_RATE_INTERVAL_FASTER_THAN_APPROVED_SCOPE:{min_iv}<{effective_scope['min_request_interval_ms']}")

    if max_req is not None and len(plan_eps) > max_req:
        viols.append(f"PLAN_BATCH_COUNT_EXCEEDS_MAX_REQUESTS:{len(plan_eps)}>{max_req}")
    if max_pages is not None and len(plan_eps) > max_pages:
        viols.append(f"PLAN_BATCH_COUNT_EXCEEDS_MAX_PAGES:{len(plan_eps)}>{max_pages}")

    projected_post_gib = 0.0
    if disk_free is not None and max_bytes is not None:
        projected_post_gib = disk_free - (max_bytes / (1024.0 ** 3)) - 0.05
        if projected_post_gib < STRICT_RUNTIME_DISK_MIN_GIB_R5:
            viols.append(f"PROJECTED_DISK_POST_CAPTURE_BELOW_20GIB:{projected_post_gib:.4f}")

    if viols:
        return False, viols, None

    return True, [], {
        "endpoints": plan_eps,
        "window_start_ms": plan_ws,
        "window_end_ms": plan_we,
        "max_requests": max_req,
        "max_total_bytes": max_bytes,
        "max_duration_sec": max_dur,
        "max_pages": max_pages,
        "max_response_bytes": max_resp,
        "max_retries_per_request": max_ret,
        "max_concurrency": max_conc,
        "min_request_interval_ms": min_iv,
        "destination_disk_free_gib": round(float(disk_free), 4),
        "projected_post_capture_disk_gib": round(float(projected_post_gib), 4),
    }


def _validate_r5_plan_binding_strict(
    plan: Any,
    trusted_scope: Optional[Any],
    now_utc: datetime,
    *,
    expected_run_id: Optional[str] = None,
    destination_root: Optional[Path] = None,
) -> Tuple[bool, List[str], Optional[R5TrustedPlanReceipt], Optional[R5TrustedScopeReceipt]]:
    """
    Validates immutable semantic + cryptographic-MAC/process-local binding between `plan`,
    `trusted_scope` (optional explicit scope decision or receipt), `now_utc`, `expected_run_id`,
    and `destination_root`. Defeats raw forged plans, mutated plans, recomputed public digests,
    expired grants, cross-run reuse, destination drift, and endpoint/URL mismatches.
    """
    if not isinstance(plan, Mapping):
        return False, ["PLAN_NOT_MAPPING"], None, None
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return False, ["INVALID_OR_NAIVE_UTC_CLOCK"], None, None

    viols: List[str] = []
    if plan.get("oos_designation_forbidden") is not True:
        viols.append("PLAN_MISSING_OOS_FORBIDDEN_LOCK")

    role = str(plan.get("partition_role") or "")
    if role not in ALLOWED_CAPTURE_PURPOSES_R5_C1:
        viols.append(f"INVALID_PLAN_PARTITION_ROLE:{role!r}")
    if _contains_forbidden_oos_token_c1(role):
        viols.append("OOS_PARTITION_ROLE_FORBIDDEN")

    scope_rec_arg: Optional[R5TrustedScopeReceipt] = None
    if trusted_scope is not None:
        if isinstance(trusted_scope, R5TrustedScopeReceipt):
            scope_rec_arg = trusted_scope
        elif isinstance(trusted_scope, Mapping):
            sr = trusted_scope.get("_trusted_scope_receipt")
            if not isinstance(sr, R5TrustedScopeReceipt):
                return False, ["FORGED_OR_MISSING_TRUSTED_SCOPE_RECEIPT"], None, None
            if trusted_scope.get("admitted") is not True:
                return False, ["TRUSTED_SCOPE_NOT_ADMITTED"], None, None
            if str(trusted_scope.get("scope_digest_sha256") or "") != sr.scope_digest_sha256:
                return False, ["TRUSTED_SCOPE_DIGEST_TAMPERED"], None, None
            scope_rec_arg = sr
        else:
            return False, ["INVALID_TRUSTED_SCOPE_TYPE"], None, None

    plan_rec_raw = plan.get("_trusted_plan_receipt")
    pr_ok, pr_viols, plan_rec, bound_scope = _GLOBAL_SCOPE_AUTHORITY_C1.verify_plan_receipt(
        plan_rec_raw, scope_receipt=scope_rec_arg, now_utc=now_utc, expected_run_id=expected_run_id
    )
    if not pr_ok or plan_rec is None or bound_scope is None:
        return False, viols + pr_viols, None, None

    # Compare public plan view strictly against BOTH its own digest AND the registered immutable snapshot!
    public_view = {k: v for k, v in plan.items() if not str(k).startswith("_") and k != "plan_digest_sha256"}
    try:
        recomputed_digest = canonical_hash(public_view)
        current_snap_json = json.dumps(public_view, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception as exc:
        return False, [f"PLAN_SERIALIZATION_ERROR:{exc}"], None, None

    if str(plan.get("plan_digest_sha256") or "") != recomputed_digest:
        viols.append("PLAN_DIGEST_MISMATCH_OR_MUTATED")
    if recomputed_digest != plan_rec.plan_digest_sha256:
        viols.append("PLAN_DIGEST_DRIFT_VS_TRUSTED_PLAN_RECEIPT")
    if current_snap_json != plan_rec.immutable_plan_snapshot_json:
        viols.append("PLAN_SNAPSHOT_MUTATED_AFTER_ISSUE")
    if str(plan.get("scope_digest_sha256") or "") != bound_scope.scope_digest_sha256:
        viols.append("PLAN_SCOPE_DIGEST_MISMATCH")

    # Enforce strict QA vs Live isolation
    live_perm = bool(plan.get("live_network_permitted", False))
    if live_perm != bound_scope.live_network_permitted:
        viols.append("PLAN_LIVE_PERMISSION_ESCALATION_MISMATCH")
    if live_perm and (role != "DEVELOPMENT_CAPTURE" or bound_scope.authorization_status != "AUTHORIZED_BOUNDED_DEV"):
        viols.append("LIVE_NETWORK_REQUIRES_VERIFIED_DEV_SCOPE")
    if not live_perm and role != "QA_FIXTURE":
        viols.append("NON_LIVE_PLAN_MUST_BE_QA_FIXTURE")

    # Destination containment / OOS token / destination_scope binding
    if destination_root is not None:
        dest_str = str(destination_root)
        if _contains_forbidden_oos_token_c1(dest_str):
            viols.append("OOS_DESTINATION_PATH_FORBIDDEN")
        if plan_rec.destination_scope is not None:
            norm_dest = str(Path(destination_root).resolve())
            norm_scope = str(Path(plan_rec.destination_scope).resolve())
            try:
                Path(norm_dest).relative_to(Path(norm_scope))
            except ValueError:
                viols.append("DESTINATION_OUTSIDE_APPROVED_DESTINATION_SCOPE")

    # Re-verify every batch's endpoint/URL/method/symbol/product/window against bound_scope + C2/C3 allowlist!
    eff = bound_scope.effective_scope
    batches = plan.get("batches")
    if not isinstance(batches, list) or len(batches) == 0:
        viols.append("EMPTY_OR_INVALID_BATCHES")
    else:
        seen_bids: set[str] = set()
        seen_eps: set[str] = set()
        for b_item in batches:
            if not isinstance(b_item, Mapping):
                viols.append("BATCH_NOT_MAPPING")
                continue
            bid = str(b_item.get("batch_id") or "")
            if not bid or bid in seen_bids:
                viols.append(f"DUPLICATE_OR_EMPTY_BATCH_ID:{bid!r}")
            seen_bids.add(bid)
            ep_k = str(b_item.get("endpoint_key") or "")
            if ep_k in seen_eps:
                viols.append(f"DUPLICATE_ENDPOINT_IN_PLAN_BATCHES:{ep_k}")
            seen_eps.add(ep_k)
            if ep_k not in eff["endpoints"] or ep_k not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
                viols.append(f"BATCH_ENDPOINT_UNAUTHORIZED:{ep_k}")
                continue
            expected_url = str(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2[ep_k]["url"])
            if str(b_item.get("url") or "") != expected_url:
                viols.append(f"BATCH_URL_KEY_MISMATCH:{ep_k}:{b_item.get('url')!r}!={expected_url!r}")
            if str(b_item.get("method") or "") != "GET":
                viols.append(f"BATCH_NON_GET_METHOD_FORBIDDEN:{b_item.get('method')!r}")
            if str(b_item.get("symbol") or "") != str(eff["symbol"]) or str(b_item.get("product_type") or "") != str(eff["product_type"]):
                viols.append("BATCH_SYMBOL_OR_PRODUCT_MISMATCH")
            b_ws = b_item.get("window_start_ms")
            b_we = b_item.get("window_end_ms")
            if not _is_strict_int(b_ws) or not _is_strict_int(b_we) or int(b_ws) < int(eff["window_start_ms"]) or int(b_we) > int(eff["window_end_ms"]) or int(b_we) <= int(b_ws):
                viols.append("BATCH_WINDOW_OUTSIDE_APPROVED_SCOPE")
            if scan_for_forbidden_secrets_r2_c1(dict(b_item)):
                viols.append(f"FORBIDDEN_SECRET_IN_BATCH:{bid}")

        bud = plan.get("budget") or {}
        if _is_strict_int(bud.get("max_requests")) and len(batches) > int(bud["max_requests"]):
            viols.append("BATCH_COUNT_EXCEEDS_PLAN_MAX_REQUESTS")
        if _is_strict_int(bud.get("max_pages")) and len(batches) > int(bud["max_pages"]):
            viols.append("BATCH_COUNT_EXCEEDS_PLAN_MAX_PAGES")

    return len(viols) == 0, viols, plan_rec, bound_scope


# =====================================================================
# 1. build_r5_source_capability_inventory
# =====================================================================
def build_r5_source_capability_inventory(
    repo_root: Path,
    verified_source_specs: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Static read-only capability audit of supported public read-only market endpoints
    anchored strictly to existing repo allowlists (`OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2`,
    `OFFICIAL_BITGET_PUBLIC_PATHS_C3`) and verified specs. NEVER invents unverified URLs,
    assumes XAUUSDT live availability, assumes generic USDT fees for gold, or claims
    historical L1 BBO REST backfill or continuous <=1s BBO over snapshot polling.
    """
    endpoints: Dict[str, Any] = {}
    for ep_key, spec in sorted(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2.items()):
        url = str(spec["url"])
        path_part = url.split(OFFICIAL_BITGET_PUBLIC_HOST_C3, 1)[-1]
        in_c3_paths = path_part in OFFICIAL_BITGET_PUBLIC_PATHS_C3
        is_quote = spec.get("data_origin") == "OBSERVED_QUOTE"
        endpoints[ep_key] = {
            "endpoint_key": ep_key,
            "venue": "BITGET_PUBLIC_REST",
            "source_category": "PUBLIC_READONLY_MARKET_DATA",
            "http_method": str(spec["method"]),
            "auth_required": bool(spec["auth_required"]),
            "read_only": bool(spec["read_only"]),
            "url": url,
            "path": path_part,
            "verified_in_c3_trust_boundary": in_c3_paths,
            "allowed_required_params": list(spec.get("required_params") or ()),
            "data_origin": str(spec.get("data_origin") or "UNKNOWN"),
            "doc_verified_utc": str(spec.get("doc_verified_utc") or "UNKNOWN"),
            "timestamp_semantics": "UTC_EPOCH_MS_EXCHANGE_REPORTED_UNVERIFIED_LIVE_UNTIL_AUTHORIZED_QUERY",
            "history_retention": "UNKNOWN_UNVERIFIED_LIVE" if not is_quote else "LATEST_SNAPSHOT_ONLY_NO_HISTORICAL_BBO",
            "continuous_1s_bbo_supported": False,
            "capability_status": (
                "SUPPORTED_PUBLIC_SNAPSHOT_ONLY_NOT_CONTINUOUS_BBO"
                if (is_quote and in_c3_paths)
                else ("SUPPORTED_PUBLIC_GET_PENDING_OWNER_AUTH" if in_c3_paths else "UNSUPPORTED_OUTSIDE_C3_BOUNDARY")
            ),
            "rate_bounds_local_guard": {"min_request_interval_ms": 50, "max_retries": 3, "max_response_bytes": MAX_RAW_HTTP_BODY_BYTES_C3},
        }

    # Explicitly record missing/unverified capabilities without fabricating endpoints!
    missing_or_blocked_domains = {
        "continuous_l1_bbo_1s_stream": {
            "status": "BLOCKED_CONTINUOUS_BBO",
            "reason": (
                "Existing C2/C3/C4 trusted transport supports bounded HTTPS GET snapshot endpoints only; "
                "public REST ticker/orderbook endpoints return latest snapshot only (not historical BBO, not continuous <=1s stream), "
                "and WebSocket streaming is NOT verified under the C4 trusted transport boundary."
            ),
        },
        "public_funding_rate_history": {
            "status": "UNSUPPORTED_OR_UNVERIFIED_ENDPOINT",
            "reason": (
                "No public funding history endpoint is currently registered in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2 / "
                "OFFICIAL_BITGET_PUBLIC_PATHS_C3. Do not invent endpoint URL or assume 8h funding interval without verified raw source proof."
            ),
        },
        "live_contract_identity_xauusdt": {
            "status": "BLOCKED_CONTRACT_IDENTITY_UNVERIFIED_LIVE",
            "reason": (
                "Local reference spec lists XAUUSDT USDT-FUTURES (tick=0.01), but live contract existence, active status, "
                "settlement rules, open-session calendar, and current tick/lot precision have NOT been queried live in this offline run."
            ),
            "local_reference_only_not_live_proof": dict(BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1),
        },
        "account_effective_fee_tier": {
            "status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "reason": "Private account endpoints are forbidden; generic public USDT-futures fee schedules MUST NOT be assumed for gold/owner account.",
        },
        "observed_execution_slippage_and_fill_mix": {
            "status": "MISSING",
            "reason": "Public market endpoints never provide owner's realized fill slippage or maker/taker execution mix.",
        },
    }

    extra_specs = dict(verified_source_specs or {})
    return {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "repo_root_checked": True,
        "external_network_queried": False,
        "public_host_allowlist": [OFFICIAL_BITGET_PUBLIC_HOST_C3],
        "c3_trusted_paths_allowlist": sorted(OFFICIAL_BITGET_PUBLIC_PATHS_C3),
        "forbidden_private_or_order_tokens": list(FORBIDDEN_URL_TOKENS_C2),
        "supported_endpoints_inventory": endpoints,
        "extra_verified_source_specs": extra_specs,
        "missing_or_blocked_capabilities": missing_or_blocked_domains,
        "supported_ohlcv_granularities_c2": sorted(GRANULARITY_TO_MS_C2.keys()),
        "r4_canonical_timeframes_target": sorted(SUPPORTED_TIMEFRAME_MS_R5.keys()),
        "status": "PASS",
    }


# =====================================================================
# 2. validate_r5_operator_capture_scope (KH-03.R5.C1 Hardened P1-A)
# =====================================================================
def validate_r5_operator_capture_scope(
    authorization: Optional[Any],
    requested_scope: Mapping[str, Any],
    now_utc: datetime,
    trust_context: Optional[Mapping[str, Any]] = None,
    *,
    attestation_verifier: Optional[R5OwnerAttestationVerifier] = None,
    grant_ledger: Optional[R5GrantConsumptionLedgerC2] = None,
) -> Dict[str, Any]:
    """
    Fail-closed operator capture scope validator (KH-03.R5.C1 hardened).
    - Strict schema/type validation: rejects bool-as-int, NaN/Inf, None, malformed/duplicate
      endpoints, invalid/inverted UTC ms window, missing mandatory scope fields, OOS tokens,
      or embedded secrets.
    - Full grant-to-request comparison: symbol, product_type, endpoints subset, window start/end
      within grant, max_requests, max_total_bytes, max_duration_sec, optional sub-caps, run_id,
      purpose, grant_id, destination_scope, and expiry (`expires_utc_ms > now_utc_ms`).
    - NEVER accepts CLI flags, env vars, prompt text, bare `R5OperatorGrant`, or caller-supplied
      `trust_context["human_operator_attestation_verified"]` dicts as proof of human approval.
    - Live path invokes an explicit `R5OwnerAttestationVerifier`; default verifier ALWAYS denies
      (`BLOCKED_OWNER_AUTHORIZATION`) until a genuine external trust boundary verifier exists.
    - Returns typed immutable `effective_scope`, audit `scope_digest_sha256`, and a process-local
      non-serializable `_trusted_scope_receipt` bound to `plan_r5_capture_batches`.
    """
    if trust_context is not None and not isinstance(trust_context, Mapping):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": ["TRUST_CONTEXT_NOT_MAPPING"],
        }
    tc = dict(trust_context or {})

    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": ["INVALID_OR_NAIVE_UTC_CLOCK"],
        }
    now_ms = int(now_utc.timestamp() * 1000)
    violations: List[str] = []

    if scan_for_forbidden_secrets_r2_c1(tc) or (isinstance(requested_scope, Mapping) and scan_for_forbidden_secrets_r2_c1(dict(requested_scope))):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "REJECTED_FORBIDDEN_SECRET",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": ["FORBIDDEN_SECRET_IN_CAPTURE_SCOPE_OR_CONTEXT"],
        }

    # Explicitly reject CLI flags / env vars / prompt pretending to be owner approval
    if tc.get("from_cli_flag") or tc.get("from_env_var") or tc.get("from_prompt_text"):
        violations.append("CLI_ENV_OR_PROMPT_NOT_VALID_HUMAN_OWNER_AUTHORIZATION")

    if authorization is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "live_network_permitted": False,
            "violations": violations + ["NO_SEPARATE_OPERATOR_AUTHORIZATION_SUPPLIED"],
        }

    if not isinstance(authorization, R5OperatorGrant):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": violations + ["AUTHORIZATION_OBJECT_TYPE_INVALID"],
        }

    # Scan grant fields for secrets
    grant_dict_for_scan = {
        "grant_id": authorization.grant_id,
        "issued_by": authorization.issued_by,
        "purpose": authorization.purpose,
        "symbol": authorization.symbol,
        "product_type": authorization.product_type,
        "run_id": authorization.run_id,
        "origin_kind": authorization.origin_kind,
        "destination_scope": authorization.destination_scope,
    }
    if scan_for_forbidden_secrets_r2_c1(grant_dict_for_scan):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "REJECTED_FORBIDDEN_SECRET",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": ["FORBIDDEN_SECRET_IN_GRANT"],
        }

    # Validate grant fields strictly (no bool-as-int, no None, no OOS, finite UTC ms)
    g_id, e = _strict_canonical_id_c1(authorization.grant_id, "grant.grant_id")
    if e:
        violations.append(e)
    g_iss, e = _strict_canonical_id_c1(authorization.issued_by, "grant.issued_by")
    if e:
        violations.append(e)
    g_purp, e = _strict_canonical_id_c1(authorization.purpose, "grant.purpose")
    if e:
        violations.append(e)
    else:
        if _contains_forbidden_oos_token_c1(g_purp or ""):
            violations.append("OOS_CAPTURE_OR_EVALUATION_FORBIDDEN_IN_R5")
        elif g_purp not in ALLOWED_CAPTURE_PURPOSES_R5_C1:
            violations.append(f"FORBIDDEN_CAPTURE_PURPOSE:{g_purp}")
    g_sym, e = _strict_canonical_id_c1(authorization.symbol, "grant.symbol")
    if e:
        violations.append(e)
    elif g_sym != g_sym.upper():
        violations.append(f"NON_CANONICAL_GRANT_SYMBOL:{g_sym!r}")
    g_pt, e = _strict_canonical_id_c1(authorization.product_type, "grant.product_type")
    if e:
        violations.append(e)
    elif g_pt not in ALLOWED_PRODUCT_TYPES_R5_C1:
        violations.append(f"UNSUPPORTED_GRANT_PRODUCT_TYPE:{g_pt!r}")
    g_rid, e = _strict_canonical_id_c1(authorization.run_id, "grant.run_id")
    if e:
        violations.append(e)
    elif _contains_forbidden_oos_token_c1(g_rid or ""):
        violations.append("OOS_TOKEN_FORBIDDEN_IN_GRANT_RUN_ID")
    if not isinstance(authorization.is_qa_fixture_grant, bool):
        violations.append("INVALID_IS_QA_FIXTURE_GRANT_BOOL")

    g_eps: List[str] = []
    if not isinstance(authorization.allowed_endpoints, (tuple, list)) or len(authorization.allowed_endpoints) == 0:
        violations.append("GRANT_EMPTY_ALLOWED_ENDPOINTS")
    else:
        seen_ge: set[str] = set()
        for ep in authorization.allowed_endpoints:
            ep_s, ep_e = _strict_canonical_id_c1(ep, "grant.allowed_endpoint")
            if ep_e:
                violations.append(ep_e)
                continue
            assert ep_s is not None
            if ep_s in seen_ge:
                violations.append(f"DUPLICATE_GRANT_ENDPOINT:{ep_s}")
                continue
            seen_ge.add(ep_s)
            if ep_s not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
                violations.append(f"GRANT_ENDPOINT_NOT_IN_ALLOWLIST:{ep_s}")
                continue
            g_eps.append(ep_s)

    g_ws: Optional[int] = None
    g_we: Optional[int] = None
    g_exp: Optional[int] = None
    for fk in ("window_start_ms", "window_end_ms", "expires_utc_ms"):
        raw_t = getattr(authorization, fk, None)
        if not _is_strict_int(raw_t):
            violations.append(f"INVALID_INT_TYPE:grant.{fk}:{type(raw_t).__name__}")
        else:
            try:
                tv = _to_utc_ms(raw_t, field=f"grant.{fk}")
                if fk == "window_start_ms":
                    g_ws = tv
                elif fk == "window_end_ms":
                    g_we = tv
                else:
                    g_exp = tv
            except ValueError as exc:
                violations.append(str(exc))
    if g_ws is not None and g_we is not None and g_we <= g_ws:
        violations.append("INVALID_CAPTURE_TIME_WINDOW")
    if g_exp is not None and g_exp <= now_ms:
        violations.append("OPERATOR_GRANT_EXPIRED")

    g_max_req, e = _strict_pos_int_c1(authorization.max_requests, "grant.max_requests", max_val=HARD_MAX_REQUESTS_R5_C1)
    if e:
        violations.append(e)
    g_max_bytes, e = _strict_pos_int_c1(authorization.max_total_bytes, "grant.max_total_bytes", max_val=HARD_MAX_TOTAL_BYTES_R5_C1)
    if e:
        violations.append(e)
    g_max_dur, e = _strict_pos_int_c1(authorization.max_duration_sec, "grant.max_duration_sec", max_val=HARD_MAX_DURATION_SEC_R5_C1)
    if e:
        violations.append(e)

    g_max_pages = g_max_req if authorization.max_pages is None else None
    if authorization.max_pages is not None:
        g_max_pages, e = _strict_pos_int_c1(authorization.max_pages, "grant.max_pages", max_val=HARD_MAX_PAGES_R5_C1)
        if e:
            violations.append(e)
    g_max_resp = DEFAULT_MAX_PAGE_BYTES_R5 if authorization.max_response_bytes is None else None
    if authorization.max_response_bytes is not None:
        g_max_resp, e = _strict_pos_int_c1(authorization.max_response_bytes, "grant.max_response_bytes", max_val=DEFAULT_MAX_PAGE_BYTES_R5)
        if e:
            violations.append(e)
    g_max_ret = HARD_MAX_RETRIES_R5_C1 if authorization.max_retries_per_request is None else None
    if authorization.max_retries_per_request is not None:
        g_max_ret, e = _strict_nonneg_int_c1(authorization.max_retries_per_request, "grant.max_retries_per_request", max_val=HARD_MAX_RETRIES_R5_C1)
        if e:
            violations.append(e)
    g_max_conc = MAX_GLOBAL_CONCURRENCY_R5 if authorization.max_concurrency is None else None
    if authorization.max_concurrency is not None:
        g_max_conc, e = _strict_pos_int_c1(authorization.max_concurrency, "grant.max_concurrency", max_val=MAX_GLOBAL_CONCURRENCY_R5)
        if e:
            violations.append(e)
    g_min_iv = HARD_MIN_REQUEST_INTERVAL_MS_R5_C1 if authorization.min_request_interval_ms is None else None
    if authorization.min_request_interval_ms is not None:
        g_min_iv, e = _strict_pos_int_c1(authorization.min_request_interval_ms, "grant.min_request_interval_ms", max_val=60_000)
        if e:
            violations.append(e)
        elif g_min_iv is not None and g_min_iv < HARD_MIN_REQUEST_INTERVAL_MS_R5_C1:
            violations.append(f"GRANT_RATE_INTERVAL_BELOW_FLOOR:{g_min_iv}")

    g_dest = authorization.destination_scope
    if g_dest is not None:
        if not isinstance(g_dest, str) or not g_dest.strip():
            violations.append("INVALID_GRANT_DESTINATION_SCOPE")
        elif _contains_forbidden_oos_token_c1(g_dest):
            violations.append("OOS_DESTINATION_SCOPE_FORBIDDEN")
        else:
            g_dest = g_dest.strip()

    # Validate & normalize requested_scope strictly
    req_ok, req_viols, req_norm = _normalize_r5_capture_scope_strict(requested_scope)
    if not req_ok or req_norm is None:
        violations.extend(req_viols)
    else:
        if g_sym is not None and req_norm["symbol"] != g_sym:
            violations.append("SYMBOL_OR_PRODUCT_TYPE_SCOPE_MISMATCH")
        if g_pt is not None and req_norm["product_type"] != g_pt:
            violations.append("SYMBOL_OR_PRODUCT_TYPE_SCOPE_MISMATCH")
        if g_purp is not None and req_norm["purpose"] != g_purp:
            violations.append("PURPOSE_SCOPE_MISMATCH")
        if g_rid is not None and req_norm["run_id"] != g_rid:
            violations.append("RUN_ID_SCOPE_MISMATCH")
        if req_norm["grant_id"] is not None and g_id is not None and req_norm["grant_id"] != g_id:
            violations.append("GRANT_ID_SCOPE_MISMATCH")
        if any(ep not in g_eps for ep in req_norm["endpoints"]):
            violations.append("REQUESTED_ENDPOINT_OUTSIDE_GRANT")
        if g_ws is not None and int(req_norm["window_start_ms"]) < g_ws:
            violations.append("REQUESTED_WINDOW_START_BEFORE_GRANT")
        if g_we is not None and int(req_norm["window_end_ms"]) > g_we:
            violations.append("REQUESTED_WINDOW_END_AFTER_GRANT")
        if g_max_req is not None and int(req_norm["max_requests"]) > g_max_req:
            violations.append("REQUESTED_REQUESTS_EXCEED_GRANT_BUDGET")
        if g_max_bytes is not None and int(req_norm["max_total_bytes"]) > g_max_bytes:
            violations.append("REQUESTED_BYTES_EXCEED_GRANT_BUDGET")
        if g_max_dur is not None and int(req_norm["max_duration_sec"]) > g_max_dur:
            violations.append("REQUESTED_DURATION_EXCEEDS_GRANT_BUDGET")
        if req_norm["max_pages"] is not None and g_max_pages is not None and int(req_norm["max_pages"]) > g_max_pages:
            violations.append("REQUESTED_PAGES_EXCEED_GRANT_BUDGET")
        if req_norm["max_response_bytes"] is not None and g_max_resp is not None and int(req_norm["max_response_bytes"]) > g_max_resp:
            violations.append("REQUESTED_RESPONSE_BYTES_EXCEED_GRANT_BUDGET")
        if req_norm["max_retries_per_request"] is not None and g_max_ret is not None and int(req_norm["max_retries_per_request"]) > g_max_ret:
            violations.append("REQUESTED_RETRIES_EXCEED_GRANT_BUDGET")
        if req_norm["max_concurrency"] is not None and g_max_conc is not None and int(req_norm["max_concurrency"]) > g_max_conc:
            violations.append("REQUESTED_CONCURRENCY_EXCEEDS_GRANT_BUDGET")
        if req_norm["min_request_interval_ms"] is not None and g_min_iv is not None and int(req_norm["min_request_interval_ms"]) < g_min_iv:
            violations.append("REQUESTED_RATE_INTERVAL_FASTER_THAN_GRANT")
        if g_dest is not None and req_norm["destination_scope"] is not None and req_norm["destination_scope"] != g_dest:
            violations.append("DESTINATION_SCOPE_MISMATCH")

    # Separate QA fixture scope vs Live DEV capture scope!
    qa_flag_req = tc.get("qa_fixture_mode")
    if authorization.is_qa_fixture_grant or qa_flag_req is True:
        qa_clean = (
            authorization.is_qa_fixture_grant is True
            and qa_flag_req is True
            and g_purp == "QA_FIXTURE"
            and tc.get("human_operator_attestation_verified") is not True
            and len(violations) == 0
            and req_norm is not None
        )
        if not qa_clean:
            return {
                "spec_version": SPEC_VERSION_KH03_R5_C1,
                "admitted": False,
                "mode": "QA_TOKEN_CANNOT_AUTHORIZE_LIVE_CAPTURE",
                "authorization_status": "INVALID",
                "live_network_permitted": False,
                "violations": violations + ["QA_FIXTURE_GRANT_ISOLATED_FROM_LIVE_TRANSPORT"],
            }
        assert req_norm is not None and g_id is not None and g_iss is not None and g_exp is not None and g_rid is not None
        eff_scope = {
            "grant_id": g_id,
            "issued_by": g_iss,
            "purpose": "QA_FIXTURE",
            "symbol": req_norm["symbol"],
            "product_type": req_norm["product_type"],
            "endpoints": list(req_norm["endpoints"]),
            "run_id": g_rid,
            "window_start_ms": int(req_norm["window_start_ms"]),
            "window_end_ms": int(req_norm["window_end_ms"]),
            "max_requests": int(req_norm["max_requests"]),
            "max_total_bytes": int(req_norm["max_total_bytes"]),
            "max_duration_sec": int(req_norm["max_duration_sec"]),
            "max_pages": int(req_norm["max_pages"] if req_norm["max_pages"] is not None else min(int(req_norm["max_requests"]), int(g_max_pages or HARD_MAX_PAGES_R5_C1))),
            "max_response_bytes": int(req_norm["max_response_bytes"] if req_norm["max_response_bytes"] is not None else int(g_max_resp or DEFAULT_MAX_PAGE_BYTES_R5)),
            "max_retries_per_request": int(req_norm["max_retries_per_request"] if req_norm["max_retries_per_request"] is not None else int(g_max_ret if g_max_ret is not None else HARD_MAX_RETRIES_R5_C1)),
            "max_concurrency": int(req_norm["max_concurrency"] if req_norm["max_concurrency"] is not None else int(g_max_conc or MAX_GLOBAL_CONCURRENCY_R5)),
            "min_request_interval_ms": int(req_norm["min_request_interval_ms"] if req_norm["min_request_interval_ms"] is not None else int(g_min_iv or HARD_MIN_REQUEST_INTERVAL_MS_R5_C1)),
            "expires_utc_ms": int(g_exp),
            "destination_scope": req_norm["destination_scope"] or g_dest,
            "is_qa_fixture_grant": True,
        }
        scope_dig = canonical_hash(eff_scope)
        grant_key, payload_dig, grant_ns = _canonical_grant_identity_c2(authorization, "QA_OFFLINE_ISOLATED_HARNESS")
        eff_ledger = grant_ledger if grant_ledger is not None else _GLOBAL_SCOPE_AUTHORITY_C1.ledger
        id_ok, id_viols = eff_ledger.register_or_check_grant_identity(
            grant_key, payload_dig, grant_id=g_id, grant_namespace=grant_ns
        )
        if not id_ok:
            return {
                "spec_version": SPEC_VERSION_KH03_R5_C2,
                "admitted": False,
                "mode": "CAPTURE_BLOCKED_GRANT_CONFLICT",
                "authorization_status": "INVALID",
                "live_network_permitted": False,
                "grant_identity_key": grant_key,
                "violations": id_viols,
            }
        rec = _GLOBAL_SCOPE_AUTHORITY_C1.issue_scope(
            mode="QA_FIXTURE_OFFLINE_ONLY",
            authorization_status="QA_FIXTURE_ISOLATED",
            live_network_permitted=False,
            grant_id=g_id,
            issued_by=g_iss,
            purpose="QA_FIXTURE",
            run_id=g_rid,
            expires_utc_ms=int(g_exp),
            effective_scope=eff_scope,
            scope_digest_sha256=scope_dig,
            verifier_id="QA_OFFLINE_ISOLATED_HARNESS",
            grant_identity_key=grant_key,
            grant_payload_digest=payload_dig,
            grant_namespace=grant_ns,
        )
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "schema_version": SCHEMA_VERSION_KH03_R5_C2,
            "admitted": True,
            "mode": "QA_FIXTURE_OFFLINE_ONLY",
            "authorization_status": "QA_FIXTURE_ISOLATED",
            "live_network_permitted": False,
            "effective_scope": dict(eff_scope),
            "scope_digest_sha256": scope_dig,
            "scope_receipt_id": rec.receipt_id,
            "grant_identity_key": grant_key,
            "grant_payload_digest": payload_dig,
            "grant_namespace": grant_ns,
            "attestation_verifier_id": rec.verifier_id,
            "validated_at_utc_ms": now_ms,
            "violations": [],
            "_trusted_scope_receipt": rec,
        }

    # Live DEV public capture MUST pass genuine external attestation verifier (default ALWAYS denies!)
    if violations or req_norm is None or g_id is None or g_iss is None or g_exp is None or g_rid is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": violations,
        }

    eff_live_scope = {
        "grant_id": g_id,
        "issued_by": g_iss,
        "purpose": "DEVELOPMENT_CAPTURE",
        "symbol": req_norm["symbol"],
        "product_type": req_norm["product_type"],
        "endpoints": list(req_norm["endpoints"]),
        "run_id": g_rid,
        "window_start_ms": int(req_norm["window_start_ms"]),
        "window_end_ms": int(req_norm["window_end_ms"]),
        "max_requests": int(req_norm["max_requests"]),
        "max_total_bytes": int(req_norm["max_total_bytes"]),
        "max_duration_sec": int(req_norm["max_duration_sec"]),
        "max_pages": int(req_norm["max_pages"] if req_norm["max_pages"] is not None else min(int(req_norm["max_requests"]), int(g_max_pages or HARD_MAX_PAGES_R5_C1))),
        "max_response_bytes": int(req_norm["max_response_bytes"] if req_norm["max_response_bytes"] is not None else int(g_max_resp or DEFAULT_MAX_PAGE_BYTES_R5)),
        "max_retries_per_request": int(req_norm["max_retries_per_request"] if req_norm["max_retries_per_request"] is not None else int(g_max_ret if g_max_ret is not None else HARD_MAX_RETRIES_R5_C1)),
        "max_concurrency": int(req_norm["max_concurrency"] if req_norm["max_concurrency"] is not None else int(g_max_conc or MAX_GLOBAL_CONCURRENCY_R5)),
        "min_request_interval_ms": int(req_norm["min_request_interval_ms"] if req_norm["min_request_interval_ms"] is not None else int(g_min_iv or HARD_MIN_REQUEST_INTERVAL_MS_R5_C1)),
        "expires_utc_ms": int(g_exp),
        "destination_scope": req_norm["destination_scope"] or g_dest,
        "is_qa_fixture_grant": False,
    }

    verifier = attestation_verifier if attestation_verifier is not None else _DEFAULT_DENY_ATTESTATION_VERIFIER
    if not hasattr(verifier, "verify_owner_attestation") or not callable(verifier.verify_owner_attestation):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "live_network_permitted": False,
            "violations": ["INVALID_OWNER_ATTESTATION_VERIFIER_INTERFACE"],
        }

    try:
        att_res = verifier.verify_owner_attestation(authorization, eff_live_scope, now_utc, tc)
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "live_network_permitted": False,
            "violations": [f"OWNER_ATTESTATION_VERIFIER_ERROR:{exc}"],
        }

    # Strict check on attestation result: never trust plain dict booleans or unverified origin!
    att_ok = (
        isinstance(att_res, R5AttestationVerificationResult)
        and att_res.verified is True
        and att_res.status == "VERIFIED_SEPARATE_OWNER_ATTESTATION"
        and att_res.grant_id == g_id
        and authorization.origin_kind == "SEPARATE_OWNER_ATTESTATION"
        and g_purp == "DEVELOPMENT_CAPTURE"
        and tc.get("qa_fixture_mode") is not True
    )
    if not att_ok:
        att_reason = att_res.reason if isinstance(att_res, R5AttestationVerificationResult) else "INVALID_ATTESTATION_RESULT_OBJECT"
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "live_network_permitted": False,
            "attestation_verifier_id": att_res.verifier_id if isinstance(att_res, R5AttestationVerificationResult) else "UNKNOWN",
            "violations": [f"GENUINE_HUMAN_OWNER_AUTHORIZATION_ABSENT_OR_UNVERIFIED:{att_reason}"],
        }

    scope_dig = canonical_hash(eff_live_scope)
    grant_key, payload_dig, grant_ns = _canonical_grant_identity_c2(authorization, att_res.verifier_id)
    eff_ledger_l = grant_ledger if grant_ledger is not None else _GLOBAL_SCOPE_AUTHORITY_C1.ledger
    id_ok, id_viols = eff_ledger_l.register_or_check_grant_identity(
        grant_key, payload_dig, grant_id=g_id, grant_namespace=grant_ns
    )
    if not id_ok:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_GRANT_CONFLICT",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "grant_identity_key": grant_key,
            "violations": id_viols,
        }
    rec = _GLOBAL_SCOPE_AUTHORITY_C1.issue_scope(
        mode="AUTHORIZED_BOUNDED_DEV_PUBLIC_CAPTURE",
        authorization_status="AUTHORIZED_BOUNDED_DEV",
        live_network_permitted=True,
        grant_id=g_id,
        issued_by=g_iss,
        purpose="DEVELOPMENT_CAPTURE",
        run_id=g_rid,
        expires_utc_ms=int(g_exp),
        effective_scope=eff_live_scope,
        scope_digest_sha256=scope_dig,
        verifier_id=att_res.verifier_id,
        grant_identity_key=grant_key,
        grant_payload_digest=payload_dig,
        grant_namespace=grant_ns,
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R5_C2,
        "schema_version": SCHEMA_VERSION_KH03_R5_C2,
        "admitted": True,
        "mode": "AUTHORIZED_BOUNDED_DEV_PUBLIC_CAPTURE",
        "authorization_status": "AUTHORIZED_BOUNDED_DEV",
        "live_network_permitted": True,
        "effective_scope": dict(eff_live_scope),
        "scope_digest_sha256": scope_dig,
        "scope_receipt_id": rec.receipt_id,
        "grant_identity_key": grant_key,
        "grant_payload_digest": payload_dig,
        "grant_namespace": grant_ns,
        "attestation_verifier_id": att_res.verifier_id,
        "validated_at_utc_ms": now_ms,
        "violations": [],
        "_trusted_scope_receipt": rec,
    }


# =====================================================================
# 3. plan_r5_capture_batches (KH-03.R5.C1 Hardened P1-A)
# =====================================================================
def plan_r5_capture_batches(
    scope_decision: Mapping[str, Any],
    capability_inventory: Mapping[str, Any],
    budget: Mapping[str, Any],
    *,
    now_utc: Optional[datetime] = None,
    grant_ledger: Optional[R5GrantConsumptionLedgerC2] = None,
) -> Dict[str, Any]:
    """
    Constructs an immutable, scope-bound capture batch plan strictly from a verified
    `R5TrustedScopeReceipt` produced by `validate_r5_operator_capture_scope`.
    - Refuses generic/forged `scope_decision` dicts lacking a valid registered scope receipt.
    - Refuses disk waivers, low disk (<20.5 GiB preflight / <20.0 GiB post-capture), bool/NaN/Inf,
      or ANY budget/symbol/product/endpoint/window broadening beyond the approved scope.
    - Explicitly labels `pages_semantics = "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION"`.
    - Registers an immutable process-local `_trusted_plan_receipt` bound to the plan snapshot.
    """
    if not isinstance(scope_decision, Mapping) or not scope_decision.get("admitted"):
        pre_viols = list(scope_decision.get("violations") or ()) if isinstance(scope_decision, Mapping) else ["SCOPE_DECISION_NOT_MAPPING"]
        if isinstance(budget, Mapping) and budget.get("owner_waive_disk_preflight") is True:
            pre_viols.append("DISK_WAIVER_STRICTLY_FORBIDDEN_IN_R5")
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "status": "BLOCKED",
            "block_reason": str(scope_decision.get("mode") if isinstance(scope_decision, Mapping) else "CAPTURE_BLOCKED_OWNER_AUTHORIZATION"),
            "partition_role": "NONE_BLOCKED",
            "live_network_permitted": False,
            "batches": [],
            "violations": pre_viols,
        }

    # Verify genuine process-local scope receipt (blocks forged dict admitted=True!)
    scope_rec_raw = scope_decision.get("_trusted_scope_receipt")
    check_now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    s_ok, s_viols, scope_rec = _GLOBAL_SCOPE_AUTHORITY_C1.verify_scope(
        scope_rec_raw,
        now_utc=check_now,
        expected_scope_digest=str(scope_decision.get("scope_digest_sha256") or ""),
    )
    if not s_ok or scope_rec is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "status": "BLOCKED",
            "block_reason": s_viols[0] if s_viols else "INVALID_TRUSTED_SCOPE_RECEIPT",
            "partition_role": "NONE_BLOCKED",
            "live_network_permitted": False,
            "batches": [],
            "violations": s_viols,
        }

    if bool(scope_decision.get("live_network_permitted", False)) != scope_rec.live_network_permitted:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "status": "BLOCKED",
            "block_reason": "SCOPE_LIVE_PERMISSION_TAMPERED",
            "partition_role": "NONE_BLOCKED",
            "live_network_permitted": False,
            "batches": [],
            "violations": ["SCOPE_LIVE_PERMISSION_TAMPERED"],
        }

    eff_ledger_p = grant_ledger if grant_ledger is not None else _GLOBAL_SCOPE_AUTHORITY_C1.ledger
    spent, spent_st = eff_ledger_p.is_grant_spent_or_claimed(scope_rec.grant_identity_key)
    if spent:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "status": "BLOCKED",
            "block_reason": f"GRANT_ALREADY_CLAIMED_OR_CONSUMED:{spent_st}",
            "partition_role": "NONE_BLOCKED",
            "live_network_permitted": False,
            "batches": [],
            "violations": [f"GRANT_ALREADY_CLAIMED_OR_CONSUMED:{spent_st}"],
        }

    eff = scope_rec.effective_scope
    is_qa = (scope_rec.mode == "QA_FIXTURE_OFFLINE_ONLY") and (not scope_rec.live_network_permitted)
    partition_role = "QA_FIXTURE" if is_qa else "DEVELOPMENT_CAPTURE"

    bud_ok, bud_viols, b_norm = _validate_r5_budget_within_scope(budget, eff)
    if not bud_ok or b_norm is None:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "status": "BLOCKED",
            "block_reason": bud_viols[0] if bud_viols else "INVALID_BUDGET",
            "partition_role": partition_role,
            "live_network_permitted": False,
            "batches": [],
            "violations": bud_viols,
        }

    if not isinstance(capability_inventory, Mapping):
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "status": "BLOCKED",
            "block_reason": "INVALID_CAPABILITY_INVENTORY",
            "partition_role": partition_role,
            "live_network_permitted": False,
            "batches": [],
            "violations": ["INVALID_CAPABILITY_INVENTORY"],
        }
    supported = capability_inventory.get("supported_endpoints_inventory") or {}
    cap_inv_digest = canonical_hash({
        "spec_version": capability_inventory.get("spec_version"),
        "public_host_allowlist": capability_inventory.get("public_host_allowlist"),
        "c3_trusted_paths_allowlist": capability_inventory.get("c3_trusted_paths_allowlist"),
    })

    violations: List[str] = []
    batches: List[Dict[str, Any]] = []
    for idx, ep in enumerate(b_norm["endpoints"]):
        if ep not in supported or ep not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
            violations.append(f"UNSUPPORTED_ENDPOINT_IN_PLAN:{ep}")
            continue
        ep_info = supported[ep]
        if not ep_info.get("verified_in_c3_trust_boundary"):
            violations.append(f"ENDPOINT_OUTSIDE_C3_TRUST_BOUNDARY:{ep}")
            continue
        canonical_url = str(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2[ep]["url"])
        if str(ep_info.get("url") or "") != canonical_url or str(ep_info.get("http_method") or "") != "GET":
            violations.append(f"CAPABILITY_INVENTORY_URL_OR_METHOD_MISMATCH:{ep}")
            continue
        if any(tok in canonical_url.lower() for tok in FORBIDDEN_URL_TOKENS_C2):
            violations.append(f"FORBIDDEN_PRIVATE_URL_TOKEN:{ep}")
            continue
        batch_entry: Dict[str, Any] = {
            "batch_id": f"R5_BATCH_{idx + 1:02d}_{ep}",
            "endpoint_key": ep,
            "url": canonical_url,
            "method": "GET",
            "symbol": str(eff["symbol"]),
            "product_type": str(eff["product_type"]),
            "window_start_ms": int(b_norm["window_start_ms"]),
            "window_end_ms": int(b_norm["window_end_ms"]),
            "max_pages": 1,
            "pages_semantics": "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION",
            "min_request_interval_ms": int(b_norm["min_request_interval_ms"]),
        }
        if ep in ("candles", "history_candles"):
            gran = str(budget.get("granularity") or "1m") if isinstance(budget, Mapping) else "1m"
            if gran not in GRANULARITY_TO_MS_C2:
                violations.append(f"UNSUPPORTED_GRANULARITY:{gran!r}")
                continue
            batch_entry["granularity"] = gran
        batches.append(batch_entry)

    if violations or not batches:
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C1,
            "status": "BLOCKED",
            "block_reason": violations[0] if violations else "EMPTY_CAPTURE_PLAN",
            "partition_role": partition_role,
            "live_network_permitted": False,
            "batches": [],
            "violations": violations,
        }

    plan_doc: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R5_C2,
        "schema_version": SCHEMA_VERSION_KH03_R5_C2,
        "status": "PLANNED_QA_OFFLINE" if is_qa else "PLANNED_DEV_BOUNDED",
        "partition_role": partition_role,
        "oos_designation_forbidden": True,
        "live_network_permitted": bool(scope_rec.live_network_permitted),
        "grant_id": scope_rec.grant_id,
        "grant_identity_key": scope_rec.grant_identity_key,
        "grant_payload_digest": scope_rec.grant_payload_digest,
        "run_id": scope_rec.run_id,
        "expires_utc_ms": int(scope_rec.expires_utc_ms),
        "destination_scope": eff.get("destination_scope"),
        "capability_inventory_digest_sha256": cap_inv_digest,
        "scope_receipt_id": scope_rec.receipt_id,
        "scope_digest_sha256": scope_rec.scope_digest_sha256,
        "pages_semantics": "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION",
        "total_planned_batches": len(batches),
        "budget": {
            "max_pages": int(b_norm["max_pages"]),
            "max_requests": int(b_norm["max_requests"]),
            "max_total_bytes": int(b_norm["max_total_bytes"]),
            "max_response_bytes": int(b_norm["max_response_bytes"]),
            "max_duration_sec": int(b_norm["max_duration_sec"]),
            "max_retries_per_request": int(b_norm["max_retries_per_request"]),
            "max_concurrency": int(b_norm["max_concurrency"]),
            "min_request_interval_ms": int(b_norm["min_request_interval_ms"]),
            "destination_disk_free_gib": float(b_norm["destination_disk_free_gib"]),
            "projected_post_capture_disk_gib": float(b_norm["projected_post_capture_disk_gib"]),
        },
        "batches": batches,
        "violations": [],
    }
    plan_digest = canonical_hash(plan_doc)
    plan_receipt = _GLOBAL_SCOPE_AUTHORITY_C1.issue_plan(
        scope_receipt=scope_rec,
        plan_public_snapshot=plan_doc,
        plan_digest_sha256=plan_digest,
    )
    plan_doc["plan_digest_sha256"] = plan_digest
    plan_doc["_trusted_plan_receipt"] = plan_receipt
    return plan_doc


# =====================================================================
# 4. validate_r5_instrument_metadata
# =====================================================================
def validate_r5_instrument_metadata(
    raw_evidence: Optional[bytes],
    expected_identity: Mapping[str, Any],
    *,
    provenance_class: str = "QA_FIXTURE",
) -> Dict[str, Any]:
    """
    Independently reparses raw instrument metadata bytes (bounded decompression),
    verifies symbol/product/settlement/perp semantics and exact Decimal tick/lot/notional
    rules, and quarantines mismatches without touching prior sealed state.
    """
    if not raw_evidence:
        return {
            "valid": False,
            "status": "BLOCKED_MISSING_METADATA_BYTES",
            "evidence_class": "MISSING",
            "quarantined": False,
            "violations": ["MISSING_RAW_INSTRUMENT_METADATA_BYTES"],
        }

    raw_sha = _sha256_bytes(raw_evidence)
    try:
        unpacked = safe_bounded_decompress_if_gzip_c3(
            raw_evidence, max_raw_bytes=DEFAULT_MAX_PAGE_BYTES_R5, max_decompressed_bytes=DEFAULT_MAX_DECOMPRESSED_BYTES_R5
        )
        payload = json.loads(unpacked.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"NONFINITE:{c}")))
    except Exception as exc:
        return {
            "valid": False,
            "status": "REJECTED_MALFORMED_OR_OVERSIZE_PAYLOAD",
            "evidence_class": "UNVERIFIED",
            "raw_sha256": raw_sha,
            "quarantined": True,
            "violations": [f"METADATA_PARSE_OR_DECOMPRESS_ERROR:{exc}"],
        }

    viols: List[str] = []
    if str(payload.get("code", "00000")) != "00000":
        viols.append(f"EXCHANGE_NON_OK_CODE:{payload.get('code')!r}")
    rows = payload.get("data")
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return {
            "valid": False, "status": "BLOCKED_CONTRACT_IDENTITY", "evidence_class": "MISSING",
            "raw_sha256": raw_sha, "quarantined": True, "violations": viols + ["EMPTY_CONTRACT_METADATA_LIST"],
        }

    exp_sym = str(expected_identity.get("symbol") or "").upper()
    exp_pt = str(expected_identity.get("product_type") or "").upper()
    exp_settle = str(expected_identity.get("settlement_currency") or "USDT").upper()
    matched_rows = [r for r in rows if isinstance(r, Mapping) and str(r.get("symbol") or "").upper() == exp_sym]
    if not matched_rows:
        return {
            "valid": False, "status": "BLOCKED_CONTRACT_IDENTITY", "evidence_class": "MISSING",
            "raw_sha256": raw_sha, "quarantined": True, "violations": viols + [f"CONTRACT_SYMBOL_NOT_FOUND:{exp_sym}"],
        }

    rec = matched_rows[0]
    got_pt = str(rec.get("productType") or rec.get("symbolType") or "").upper()
    got_quote = str(rec.get("quoteCoin") or rec.get("marginCoin") or "").upper()
    if exp_pt and got_pt and (exp_pt not in got_pt and got_pt not in ("PERPETUAL", "USDT-FUTURES")):
        viols.append(f"PRODUCT_TYPE_MISMATCH:got={got_pt}!=exp={exp_pt}")
    if exp_settle and got_quote and got_quote != exp_settle:
        viols.append(f"SETTLEMENT_CURRENCY_MISMATCH:got={got_quote}!=exp={exp_settle}")

    # Parse exact Decimal precision fields
    try:
        price_place = int(rec.get("pricePlace")) if rec.get("pricePlace") is not None else None
        price_end_step = _dec(rec.get("priceEndStep", 1), field="priceEndStep")
        if rec.get("tickSize") is not None:
            tick_size = _dec(rec["tickSize"], field="tickSize")
        elif price_place is not None and price_place >= 0:
            tick_size = (Decimal("10") ** (-price_place)) * price_end_step
        else:
            raise ValueError("MISSING_TICK_SIZE")
        qty_step = _dec(rec.get("sizeMultiplier") or rec.get("qtyStep"), field="sizeMultiplier")
        min_qty = _dec(rec.get("minTradeNum") or rec.get("minQty"), field="minTradeNum")
        min_notional = _dec(rec.get("minTradeUSDT") or rec.get("minNotional") or "0", field="minTradeUSDT")
        if tick_size <= 0 or qty_step <= 0 or min_qty <= 0 or min_notional < 0:
            viols.append("NON_POSITIVE_CONTRACT_PRECISION_FIELDS")
    except Exception as exc:
        viols.append(f"CONTRACT_PRECISION_PARSE_FAIL:{exc}")
        tick_size, qty_step, min_qty, min_notional = Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")

    if expected_identity.get("expected_tick_size") is not None:
        exp_tick = _dec(expected_identity["expected_tick_size"], field="expected_tick_size")
        if tick_size != exp_tick:
            viols.append(f"TICK_SIZE_PRECISION_MISMATCH:got={tick_size}!=exp={exp_tick}")

    trig_src = str(rec.get("stopTriggerPriceSource") or "UNKNOWN")
    eff_ts = rec.get("effectiveTimestampMs")
    eff_utc = None
    if eff_ts is not None:
        try:
            eff_utc = _to_utc_ms(eff_ts, field="effectiveTimestampMs")
        except ValueError as exc:
            viols.append(str(exc))

    ev_cls = provenance_class if provenance_class in EVIDENCE_CLASSES_R5 else "UNVERIFIED"
    ok = len(viols) == 0
    return {
        "valid": ok,
        "status": "PASS" if ok else "QUARANTINED_METADATA_MISMATCH",
        "evidence_class": ev_cls if ok else "UNVERIFIED",
        "quarantined": not ok,
        "raw_sha256": raw_sha,
        "normalized_metadata": {
            "symbol": exp_sym,
            "product_type": got_pt or exp_pt or "UNKNOWN",
            "settlement_currency": got_quote or "UNKNOWN",
            "contract_type": str(rec.get("symbolType") or "UNKNOWN"),
            "tick_size": str(tick_size),
            "qty_step": str(qty_step),
            "min_qty": str(min_qty),
            "min_notional_usd": str(min_notional),
            "max_leverage": str(rec.get("maxLever")) if rec.get("maxLever") is not None else "UNKNOWN",
            "stop_trigger_price_source": trig_src,
            "effective_timestamp_ms": eff_utc,
            "used_in_production_sizing": False,
        },
        "violations": viols,
    }


# =====================================================================
# 5. validate_r5_ohlcv_stream
# =====================================================================
def validate_r5_ohlcv_stream(
    raw_pages: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    timeframe: str,
    requested_range: Mapping[str, Any],
    *,
    provenance_class: str = "QA_FIXTURE",
    is_derived_aggregate: bool = False,
) -> Dict[str, Any]:
    """
    Bounded strict OHLCV validator with exact Decimal OHLC invariants, explicit UTC closed-bar
    semantics, duplicate-exact accounting vs conflicting-duplicate fail-closed, gap ledger
    (never forward-filled!), and raw->canonical SHA-256 lineage.
    """
    viols: List[str] = []
    if timeframe not in SUPPORTED_TIMEFRAME_MS_R5:
        return {"valid": False, "status": "FAIL", "violations": [f"UNSUPPORTED_TIMEFRAME:{timeframe}"]}
    step_ms = SUPPORTED_TIMEFRAME_MS_R5[timeframe]
    exp_sym = str(contract.get("symbol") or "").upper()
    tick = _dec(contract.get("tick_size", "0.01"), field="tick_size")
    req_start = _to_utc_ms(requested_range["start_ms"], field="start_ms")
    req_end = _to_utc_ms(requested_range["end_ms"], field="end_ms")
    now_ms = _to_utc_ms(requested_range["now_utc_ms"], field="now_utc_ms") if requested_range.get("now_utc_ms") is not None else req_end

    if req_end <= req_start:
        return {"valid": False, "status": "FAIL", "violations": ["INVALID_REQUESTED_RANGE"]}

    page_lineage: List[Dict[str, Any]] = []
    by_open_ts: Dict[int, Dict[str, Any]] = {}
    raw_rows_total = 0
    exact_dups = 0
    conflict_dups = 0
    out_of_order_in_raw = 0

    for p_idx, page in enumerate(raw_pages):
        raw_b = page.get("raw_bytes")
        if isinstance(raw_b, (bytes, bytearray)):
            raw_sha = _sha256_bytes(bytes(raw_b))
            try:
                unpacked = safe_bounded_decompress_if_gzip_c3(bytes(raw_b))
                parsed = json.loads(unpacked.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"NONFINITE:{c}")))
            except Exception as exc:
                viols.append(f"PAGE_{p_idx}_DECOMPRESS_OR_PARSE_ERROR:{exc}")
                continue
            rows_in = parsed.get("data") if isinstance(parsed, Mapping) else parsed
            page_sym = str(page.get("symbol") or (parsed.get("symbol") if isinstance(parsed, Mapping) else exp_sym) or exp_sym).upper()
            page_tf = str(page.get("timeframe") or timeframe)
        else:
            rows_in = page.get("rows") or ()
            raw_sha = canonical_hash(list(rows_in))
            page_sym = str(page.get("symbol") or exp_sym).upper()
            page_tf = str(page.get("timeframe") or timeframe)

        if page_sym != exp_sym:
            viols.append(f"PAGE_{p_idx}_SYMBOL_MISMATCH:{page_sym}!={exp_sym}")
        if page_tf != timeframe:
            viols.append(f"PAGE_{p_idx}_TIMEFRAME_MISMATCH:{page_tf}!={timeframe}")
        if not isinstance(rows_in, list):
            viols.append(f"PAGE_{p_idx}_ROWS_NOT_LIST")
            continue

        page_lineage.append({"page_index": p_idx, "raw_sha256": raw_sha, "raw_row_count": len(rows_in)})
        prev_ts_page: Optional[int] = None

        for r_idx, row in enumerate(rows_in):
            raw_rows_total += 1
            try:
                if isinstance(row, (list, tuple)):
                    if len(row) < 6:
                        raise ValueError("SHORT_LIST_BAR")
                    ots = _to_utc_ms(row[0], field="open_time")
                    cts = ots + step_ms
                    o, h, l, c, v = _dec(row[1], field="o"), _dec(row[2], field="h"), _dec(row[3], field="l"), _dec(row[4], field="c"), _dec(row[5], field="v")
                    is_closed = True if len(row) < 7 else bool(row[6])
                elif isinstance(row, Mapping):
                    if row.get("symbol") and str(row["symbol"]).upper() != exp_sym:
                        raise ValueError(f"ROW_SYMBOL_MISMATCH:{row['symbol']}")
                    if row.get("timeframe") and str(row["timeframe"]) != timeframe:
                        raise ValueError(f"ROW_TIMEFRAME_MISMATCH:{row['timeframe']}")
                    ots = _to_utc_ms(row.get("open_time", row.get("timestamp")), field="open_time")
                    cts = _to_utc_ms(row["close_time"], field="close_time") if row.get("close_time") is not None else (ots + step_ms)
                    if cts != ots + step_ms:
                        raise ValueError(f"CLOSE_TIME_STEP_MISMATCH:{cts - ots}!={step_ms}")
                    o, h, l, c, v = _dec(row["open"], field="open"), _dec(row["high"], field="high"), _dec(row["low"], field="low"), _dec(row["close"], field="close"), _dec(row["volume"], field="volume")
                    is_closed = bool(row.get("is_closed", True))
                else:
                    raise ValueError("UNSUPPORTED_BAR_ROW_TYPE")
            except Exception as exc:
                viols.append(f"PAGE_{p_idx}_ROW_{r_idx}_SCHEMA_ERR:{exc}")
                continue

            if prev_ts_page is not None and ots < prev_ts_page:
                out_of_order_in_raw += 1
            prev_ts_page = ots

            if not is_closed or cts > now_ms:
                viols.append(f"PARTIAL_OR_FUTURE_BAR_REJECTED:ots={ots},cts={cts},now={now_ms}")
                continue
            if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v < 0:
                viols.append(f"NONPOSITIVE_PRICE_OR_NEG_VOL:ots={ots}")
                continue
            if h < l or h < o or h < c or l > o or l > c:
                viols.append(f"OHLC_IMPOSSIBILITY:ots={ots}")
                continue
            if not all(_tick_aligned(px, tick) for px in (o, h, l, c)):
                viols.append(f"TICK_PRECISION_MISMATCH:ots={ots}")
                continue

            norm = {
                "symbol": exp_sym, "timeframe": timeframe, "open_time": ots, "close_time": cts,
                "open": str(o), "high": str(h), "low": str(l), "close": str(c), "volume": str(v),
                "is_closed": True, "ny_date": _ny_date_from_utc_ms(ots),
            }
            if ots in by_open_ts:
                if by_open_ts[ots] == norm:
                    exact_dups += 1
                else:
                    conflict_dups += 1
                    viols.append(f"CONFLICTING_DUPLICATE_BAR:ots={ots}")
            else:
                by_open_ts[ots] = norm

    ordered = [by_open_ts[k] for k in sorted(by_open_ts.keys())]
    gaps: List[Dict[str, Any]] = []
    missing_bars_total = 0
    for b0, b1 in zip(ordered, ordered[1:]):
        dt = int(b1["open_time"]) - int(b0["open_time"])
        if dt > step_ms:
            miss = (dt // step_ms) - 1
            missing_bars_total += miss
            gaps.append({
                "gap_start_ms": int(b0["close_time"]),
                "gap_end_ms": int(b1["open_time"]),
                "missing_bars": miss,
                "classification": "UNKNOWN_GAP_NO_MAINTENANCE_EVIDENCE",
            })

    expected_slots = max(1, (req_end - req_start) // step_ms)
    in_range_cnt = sum(1 for b in ordered if req_start <= int(b["open_time"]) < req_end)
    ev_cls = "DERIVED_FROM_OBSERVED" if (is_derived_aggregate and provenance_class == "OBSERVED_VERIFIED") else provenance_class
    ok = (len(viols) == 0 and conflict_dups == 0 and len(ordered) > 0)

    return {
        "valid": ok,
        "status": "PASS" if ok else "FAIL",
        "evidence_class": ev_cls if ok else "UNVERIFIED",
        "is_derived_aggregate": is_derived_aggregate,
        "forward_fill_applied": False,
        "symbol": exp_sym,
        "timeframe": timeframe,
        "step_ms": step_ms,
        "raw_rows_total": raw_rows_total,
        "normalized_unique_bars_count": len(ordered),
        "exact_duplicate_rows_deduplicated": exact_dups,
        "conflicting_duplicate_rows": conflict_dups,
        "raw_out_of_order_transitions": out_of_order_in_raw,
        "first_open_ms": int(ordered[0]["open_time"]) if ordered else None,
        "last_close_ms": int(ordered[-1]["close_time"]) if ordered else None,
        "distinct_ny_days": sorted({b["ny_date"] for b in ordered}),
        "distinct_ny_days_count": len({b["ny_date"] for b in ordered}),
        "expected_slots_in_requested_range": expected_slots,
        "observed_bars_in_requested_range": in_range_cnt,
        "coverage_ratio_in_requested_range": round(in_range_cnt / expected_slots, 4),
        "gap_intervals_count": len(gaps),
        "missing_bars_internal_gaps": missing_bars_total,
        "gaps_ledger": gaps,
        "page_lineage": page_lineage,
        "canonical_normalized_sha256": canonical_hash(ordered),
        "violations": viols,
    }


# =====================================================================
# 6. validate_r5_bbo_stream
# =====================================================================
def validate_r5_bbo_stream(
    raw_quotes: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    clock_policy: Optional[Mapping[str, Any]] = None,
    *,
    provenance_class: str = "QA_FIXTURE",
    stream_mode: str = "SAMPLED_POLL_QUOTES",
) -> Dict[str, Any]:
    """
    Validates raw L1 BBO quotes with exact Decimal bid/ask, source & local receive timestamps,
    crossed/stale/negative-latency/out-of-order diagnostics, and honest coverage denominator
    over unique expected 1s in-session grid slots (never inflated by duplicate timestamps!).
    NEVER infers BBO from OHLCV; classifies single snapshot separately from continuous stream.
    """
    cp = dict(clock_policy or {})
    max_stale_ms = int(cp.get("stale_threshold_ms", 15_000))
    max_neg_lat_ms = int(cp.get("max_negative_latency_ms", 0))
    cadence_ms = int(cp.get("expected_cadence_ms", 1_000))
    min_cov_target = float(cp.get("min_coverage_ratio_target", 0.99))
    exp_sym = str(contract.get("symbol") or "").upper()
    tick = _dec(contract.get("tick_size", "0.01"), field="tick_size")

    viols: List[str] = []
    valid_quotes: List[Dict[str, Any]] = []
    locked_cnt = 0
    crossed_cnt = 0
    stale_cnt = 0
    neg_lat_cnt = 0
    out_of_order_cnt = 0
    duplicate_ts_cnt = 0
    seen_src_ts: set[int] = set()
    prev_src_ts: Optional[int] = None

    for idx, q in enumerate(raw_quotes):
        if not isinstance(q, Mapping):
            viols.append(f"QUOTE_{idx}_NOT_MAPPING")
            continue
        if q.get("inferred_from_ohlcv") is True or str(q.get("quote_origin") or "").upper() == "OHLCV_SYNTHETIC":
            viols.append(f"OHLCV_INFERRED_BBO_FORBIDDEN:idx={idx}")
            continue
        if q.get("symbol") and str(q["symbol"]).upper() != exp_sym:
            viols.append(f"QUOTE_SYMBOL_MISMATCH:idx={idx}")
            continue
        try:
            bid = _dec(q["bid"], field="bid")
            ask = _dec(q["ask"], field="ask")
            src_ts = _to_utc_ms(q["source_timestamp_ms"], field="source_timestamp_ms")
            rcv_ts = _to_utc_ms(q["local_receive_timestamp_ms"], field="local_receive_timestamp_ms")
        except Exception as exc:
            viols.append(f"QUOTE_{idx}_PARSE_ERR:{exc}")
            continue

        if bid <= 0 or ask <= 0:
            viols.append(f"NONPOSITIVE_BBO_PRICE:idx={idx}")
            continue
        if not (_tick_aligned(bid, tick) and _tick_aligned(ask, tick)):
            viols.append(f"BBO_TICK_PRECISION_MISMATCH:idx={idx}")
            continue
        if bid > ask:
            crossed_cnt += 1
            viols.append(f"CROSSED_BBO_REJECTED:idx={idx}:bid={bid}>ask={ask}")
            continue
        if bid == ask:
            locked_cnt += 1

        lat_ms = rcv_ts - src_ts
        if lat_ms < -max_neg_lat_ms:
            neg_lat_cnt += 1
            viols.append(f"NEGATIVE_CLOCK_LATENCY:idx={idx}:lat_ms={lat_ms}")
            continue
        if lat_ms > max_stale_ms:
            stale_cnt += 1
            continue

        if prev_src_ts is not None and src_ts < prev_src_ts:
            out_of_order_cnt += 1
        prev_src_ts = src_ts

        if src_ts in seen_src_ts:
            duplicate_ts_cnt += 1
            continue
        seen_src_ts.add(src_ts)

        spread = ask - bid
        valid_quotes.append({
            "symbol": exp_sym, "bid": str(bid), "ask": str(ask), "spread_usd": str(spread),
            "source_timestamp_ms": src_ts, "local_receive_timestamp_ms": rcv_ts,
            "latency_ms": lat_ms, "sequence": q.get("sequence"), "ny_date": _ny_date_from_utc_ms(src_ts),
        })

    valid_quotes.sort(key=lambda r: int(r["source_timestamp_ms"]))
    intervals = [
        int(b["source_timestamp_ms"]) - int(a["source_timestamp_ms"])
        for a, b in zip(valid_quotes, valid_quotes[1:])
    ]
    latencies = [float(q["latency_ms"]) for q in valid_quotes]
    spreads = [float(q["spread_usd"]) for q in valid_quotes]

    # Unique 1s grid buckets (deduped so duplicate timestamps can NEVER inflate coverage >1.0!)
    win_start = cp.get("window_start_ms") or (valid_quotes[0]["source_timestamp_ms"] if valid_quotes else None)
    win_end = cp.get("window_end_ms") or (valid_quotes[-1]["source_timestamp_ms"] if valid_quotes else None)
    if win_start is not None and win_end is not None and int(win_end) >= int(win_start):
        expected_grid_slots = max(1, ((int(win_end) - int(win_start)) // cadence_ms) + 1)
        covered_buckets = {
            (int(q["source_timestamp_ms"]) - int(win_start)) // cadence_ms
            for q in valid_quotes
            if int(win_start) <= int(q["source_timestamp_ms"]) <= int(win_end)
        }
        cov_ratio = round(len(covered_buckets) / expected_grid_slots, 4)
    else:
        expected_grid_slots = 0
        covered_buckets = set()
        cov_ratio = 0.0

    # Honest BBO coverage classification
    if len(valid_quotes) == 0:
        bbo_class = "MISSING"
    elif len(valid_quotes) == 1 or stream_mode == "SINGLE_OBSERVED_SNAPSHOT":
        bbo_class = "SINGLE_OBSERVED_SNAPSHOT"
    elif stream_mode != "CONTINUOUS_TRUSTED_STREAM":
        bbo_class = "PARTIAL_SAMPLED" if cov_ratio < min_cov_target else "SAMPLED_POLL_ONLY_NOT_CONTINUOUS_STREAM"
    elif cov_ratio >= min_cov_target and (max(intervals, default=0) <= cadence_ms * 2) and len(viols) == 0:
        bbo_class = "VERIFIED_COVERAGE_FOR_DECLARED_WINDOW"
    else:
        bbo_class = "PARTIAL_SAMPLED"

    meets_1s_continuous_target = (bbo_class == "VERIFIED_COVERAGE_FOR_DECLARED_WINDOW" and provenance_class == "OBSERVED_VERIFIED")
    ok = (len(viols) == 0 and len(valid_quotes) > 0)

    return {
        "valid": ok,
        "status": "PASS" if ok else "FAIL",
        "evidence_class": provenance_class if ok else "UNVERIFIED",
        "stream_mode": stream_mode,
        "bbo_coverage_classification": bbo_class,
        "meets_continuous_1s_observed_target": meets_1s_continuous_target,
        "strategy_decision_fill_coverage": "UNKNOWN_NO_AUTHORIZED_ALIGNMENT_PROTOCOL",
        "interpolation_or_ohlcv_inference_applied": False,
        "raw_quotes_input_count": len(raw_quotes),
        "valid_unique_timestamp_quotes_count": len(valid_quotes),
        "duplicate_timestamp_quotes_excluded": duplicate_ts_cnt,
        "locked_quotes_count": locked_cnt,
        "crossed_quotes_rejected": crossed_cnt,
        "stale_quotes_excluded": stale_cnt,
        "negative_latency_rejected": neg_lat_cnt,
        "out_of_order_count": out_of_order_cnt,
        "expected_grid_slots": expected_grid_slots,
        "covered_unique_grid_slots": len(covered_buckets),
        "grid_coverage_ratio": cov_ratio,
        "max_gap_ms": max(intervals, default=0),
        "sampling_interval_ms_p50": _nearest_quantile(intervals, 0.50),
        "sampling_interval_ms_p95": _nearest_quantile(intervals, 0.95),
        "latency_ms_p50": _nearest_quantile(latencies, 0.50),
        "latency_ms_p95": _nearest_quantile(latencies, 0.95),
        "spread_usd_p50": _nearest_quantile(spreads, 0.50),
        "spread_usd_p95": _nearest_quantile(spreads, 0.95),
        "canonical_normalized_sha256": canonical_hash(valid_quotes),
        "violations": viols,
    }


# =====================================================================
# 7. validate_r5_funding_history
# =====================================================================
def validate_r5_funding_history(
    raw_pages: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    interval_spec: Optional[Mapping[str, Any]],
    *,
    provenance_class: str = "QA_FIXTURE",
) -> Dict[str, Any]:
    """
    Validates timestamped funding history with exact Decimal rates, explicit ACTUAL vs PREDICTED
    distinction (rejecting predicted rates treated as settled), explicit missing settlement ledger
    (NEVER zero-filled, NEVER assuming 8h cadence without verified `interval_spec`).
    """
    viols: List[str] = []
    exp_sym = str(contract.get("symbol") or "").upper()
    schedule_verified = bool(interval_spec and _is_strict_int(interval_spec.get("interval_ms")) and int(interval_spec["interval_ms"]) > 0)
    interval_ms = int(interval_spec["interval_ms"]) if schedule_verified else None

    by_ts: Dict[int, Dict[str, Any]] = {}
    predicted_rejected = 0
    exact_dups = 0
    conflict_dups = 0

    for p_idx, page in enumerate(raw_pages):
        rows = page.get("rows") or ()
        for r_idx, row in enumerate(rows):
            if not isinstance(row, Mapping):
                viols.append(f"FUNDING_ROW_NOT_OBJ:p={p_idx},r={r_idx}")
                continue
            if row.get("symbol") and str(row["symbol"]).upper() != exp_sym:
                viols.append(f"FUNDING_SYMBOL_MISMATCH:p={p_idx},r={r_idx}")
                continue
            rate_kind = str(row.get("rate_kind") or "ACTUAL_SETTLED").upper()
            if rate_kind != "ACTUAL_SETTLED":
                predicted_rejected += 1
                viols.append(f"PREDICTED_FUNDING_CANNOT_BE_SETTLED:p={p_idx},r={r_idx}:kind={rate_kind}")
                continue
            units = str(row.get("units") or "DECIMAL_FRACTION").upper()
            if units != "DECIMAL_FRACTION":
                viols.append(f"UNVERIFIED_FUNDING_UNITS:p={p_idx},r={r_idx}:units={units}")
                continue
            try:
                sts = _to_utc_ms(row["settlement_timestamp_ms"], field="settlement_timestamp_ms")
                rate = _dec(row["funding_rate"], field="funding_rate")
            except Exception as exc:
                viols.append(f"FUNDING_PARSE_ERR:p={p_idx},r={r_idx}:{exc}")
                continue
            if abs(rate) > Decimal("0.10"):
                viols.append(f"FUNDING_RATE_OUT_OF_SANE_FRACTION_BOUNDS:{rate}")
                continue

            norm = {
                "symbol": exp_sym, "settlement_timestamp_ms": sts, "funding_rate": str(rate),
                "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION",
                "sign_convention": str(row.get("sign_convention") or "POSITIVE_LONGS_PAY_SHORTS"),
            }
            if sts in by_ts:
                if by_ts[sts] == norm:
                    exact_dups += 1
                else:
                    conflict_dups += 1
                    viols.append(f"FUNDING_CONFLICTING_DUPLICATE:ts={sts}")
            else:
                by_ts[sts] = norm

    ordered = [by_ts[k] for k in sorted(by_ts.keys())]
    missing_settlements: List[int] = []
    if schedule_verified and interval_ms is not None and len(ordered) >= 2:
        t_cur = int(ordered[0]["settlement_timestamp_ms"])
        t_end = int(ordered[-1]["settlement_timestamp_ms"])
        present = set(by_ts.keys())
        while t_cur <= t_end:
            if t_cur not in present:
                missing_settlements.append(t_cur)
            t_cur += interval_ms
    elif schedule_verified and interval_ms is not None and interval_spec.get("window_start_ms") and interval_spec.get("window_end_ms"):
        t_cur = _to_utc_ms(interval_spec["window_start_ms"], field="window_start_ms")
        t_end = _to_utc_ms(interval_spec["window_end_ms"], field="window_end_ms")
        present = set(by_ts.keys())
        while t_cur <= t_end:
            if t_cur not in present:
                missing_settlements.append(t_cur)
            t_cur += interval_ms

    if not ordered:
        readiness = "MISSING"
    elif not schedule_verified:
        readiness = "UNKNOWN_SCHEDULE"
    elif len(missing_settlements) > 0:
        readiness = "PARTIAL"
    elif len(viols) == 0:
        readiness = "VERIFIED_DECLARED_WINDOW"
    else:
        readiness = "FAIL"

    ok = (len(viols) == 0 and len(ordered) > 0 and schedule_verified and len(missing_settlements) == 0)
    return {
        "valid": ok,
        "status": "PASS" if ok else ("INCOMPLETE" if (len(viols) == 0 and readiness in ("MISSING", "PARTIAL", "UNKNOWN_SCHEDULE")) else "FAIL"),
        "evidence_class": provenance_class if ok else ("MISSING" if not ordered else "UNVERIFIED"),
        "funding_readiness": readiness,
        "schedule_verified": schedule_verified,
        "verified_interval_ms": interval_ms,
        "settled_records_count": len(ordered),
        "predicted_records_rejected": predicted_rejected,
        "exact_duplicates_deduplicated": exact_dups,
        "conflicting_duplicates": conflict_dups,
        "missing_settlement_timestamps_ms": missing_settlements,
        "missing_settlements_count": len(missing_settlements),
        "zero_fill_applied": False,
        "retroactive_r4_pnl_modified": False,
        "canonical_normalized_sha256": canonical_hash(ordered),
        "violations": viols,
    }


# =====================================================================
# 8. validate_r5_owner_fee_confirmation
# =====================================================================
def validate_r5_owner_fee_confirmation(
    document: Optional[Mapping[str, Any]],
    expected_contract: Mapping[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    """
    Validates a voluntarily supplied REDACTED owner fee confirmation.
    - Default when `document is None`: `UNKNOWN_NOT_LOCALLY_VERIFIED` (valid honest unknown!).
    - Rejects any embedded secret/API key/password/account ID/balance.
    - Rejects generic public fee schedules masquerading as account-effective fee!
    - Supports negative maker rebate in exact Decimal + explicit bps conversion.
    - Distinguishes `OWNER_ATTESTED` vs `INDEPENDENTLY_VERIFIED` (never claims cryptographic
      signature verification if no real verifier exists!).
    - Marks `store_local_only = True` (never upload raw personal fee evidence to GitHub!).
    """
    if document is None:
        return {
            "valid": False,
            "status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "account_fee_readiness": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "evidence_class": "MISSING",
            "credentials_requested": False,
            "store_local_only": True,
            "violations": [],
        }

    viols: List[str] = []
    # Scan for forbidden secrets or private personal account fields
    if scan_for_forbidden_secrets_r2_c1(dict(document)):
        viols.append("FORBIDDEN_SECRET_DETECTED_IN_FEE_DOCUMENT")
    lower_keys = {str(k).lower() for k in document.keys()}
    for fk in FORBIDDEN_FEE_SENSITIVE_KEYS:
        if any(fk in lk for lk in lower_keys):
            viols.append(f"SENSITIVE_PRIVATE_FIELD_FORBIDDEN:{fk}")

    if document.get("source_type") in ("PUBLIC_GENERIC_SCHEDULE", "ASSUMED_DEFAULT"):
        viols.append("PUBLIC_GENERIC_FEE_CANNOT_BE_ACCOUNT_EFFECTIVE")

    exp_sym = str(expected_contract.get("symbol") or "").upper()
    exp_pt = str(expected_contract.get("product_type") or "").upper()
    if str(document.get("symbol") or "").upper() != exp_sym or str(document.get("product_type") or "").upper() != exp_pt:
        viols.append("FEE_DOCUMENT_CONTRACT_MISMATCH")

    try:
        maker = _dec(document["maker_fee_rate"], field="maker_fee_rate")
        taker = _dec(document["taker_fee_rate"], field="taker_fee_rate")
        if taker < Decimal("0") or taker > Decimal("0.05") or maker < Decimal("-0.01") or maker > Decimal("0.05"):
            viols.append("FEE_RATE_OUT_OF_BOUNDS")
    except Exception as exc:
        viols.append(f"FEE_RATE_PARSE_ERR:{exc}")
        maker, taker = Decimal("0"), Decimal("0")

    eff_date_str = str(document.get("effective_date_utc") or "")
    try:
        eff_dt = datetime.fromisoformat(eff_date_str.replace("Z", "+00:00"))
        if eff_dt.tzinfo is None:
            raise ValueError("NAIVE_TIMESTAMP")
        if eff_dt > now:
            viols.append("FUTURE_EFFECTIVE_DATE_REJECTED")
    except Exception as exc:
        viols.append(f"INVALID_EFFECTIVE_DATE:{exc}")

    ev_hash = str(document.get("source_evidence_sha256") or "")
    if len(ev_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in ev_hash):
        viols.append("INVALID_SOURCE_EVIDENCE_SHA256")
    if not str(document.get("tier_or_region_scope") or "").strip():
        viols.append("MISSING_TIER_OR_REGION_SCOPE")
    if document.get("redacted_no_secrets_confirmed") is not True:
        viols.append("MISSING_REDACTION_CONFIRMATION")

    att_mode = str(document.get("attestation_mode") or "OWNER_ATTESTED")
    if att_mode == "INDEPENDENTLY_VERIFIED" and not document.get("cryptographic_verifier_receipt"):
        viols.append("FALSE_INDEPENDENT_VERIFICATION_CLAIM_WITHOUT_VERIFIER")

    if viols:
        return {
            "valid": False,
            "status": "REJECTED",
            "account_fee_readiness": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "evidence_class": "UNVERIFIED",
            "credentials_requested": False,
            "store_local_only": True,
            "violations": viols,
        }

    is_qa = bool(document.get("is_qa_fixture", False))
    ev_cls = "QA_FIXTURE" if is_qa else ("OWNER_ATTESTED" if att_mode == "OWNER_ATTESTED" else "OBSERVED_VERIFIED")
    readiness = "UNKNOWN_NOT_LOCALLY_VERIFIED" if is_qa else ("OWNER_ATTESTED" if att_mode == "OWNER_ATTESTED" else "VERIFIED")
    return {
        "valid": True,
        "status": "QA_FIXTURE_VALIDATED_NOT_REAL_ACCOUNT" if is_qa else "OWNER_ATTESTED_VALIDATED",
        "account_fee_readiness": readiness,
        "evidence_class": ev_cls,
        "maker_fee_rate": str(maker),
        "taker_fee_rate": str(taker),
        "maker_fee_bps": str((maker * Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "taker_fee_bps": str((taker * Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "maker_is_rebate": maker < Decimal("0"),
        "effective_date_utc": eff_date_str,
        "tier_or_region_scope": str(document["tier_or_region_scope"]),
        "redacted_digest_sha256": canonical_hash({"maker": str(maker), "taker": str(taker), "eff": eff_date_str, "ev_sha": ev_hash}),
        "credentials_requested": False,
        "store_local_only": True,
        "violations": [],
    }


# =====================================================================
# 9. build_r5_market_data_coverage_report
# =====================================================================
def build_r5_market_data_coverage_report(
    manifests: Mapping[str, Any],
    session_calendar: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Aggregates coverage diagnostics across observed/QA OHLCV, BBO, and funding manifests
    with DST-aware NY trading days (`America/New_York`), gap classifications, and explicit
    `INSUFFICIENT_COVERAGE` when below R4's >=60 independent NY-day DEV research target.
    Computes ZERO strategy metrics on new data!
    """
    ohlcv_m = manifests.get("ohlcv") or {}
    bbo_m = manifests.get("bbo") or {}
    fund_m = manifests.get("funding") or {}

    observed_ohlcv_ny_days = (
        int(ohlcv_m.get("distinct_ny_days_count", 0))
        if ohlcv_m.get("evidence_class") == "OBSERVED_VERIFIED"
        else 0
    )
    qa_ohlcv_ny_days = (
        int(ohlcv_m.get("distinct_ny_days_count", 0))
        if ohlcv_m.get("evidence_class") == "QA_FIXTURE"
        else 0
    )
    target_dev_ny_days = 60
    cov_verdict = (
        "MEETS_60_NY_DAY_PLANNING_MINIMUM_NOT_POWER_PROOF"
        if observed_ohlcv_ny_days >= target_dev_ny_days
        else "INSUFFICIENT_COVERAGE"
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "timezone_rule": "America/New_York (DST-aware via stdlib zoneinfo)",
        "session_calendar_supplied": bool(session_calendar),
        "strategy_metrics_computed_on_new_data": False,
        "r4_dev_expansion_planning_target_ny_days": target_dev_ny_days,
        "observed_verified_ohlcv_ny_days": observed_ohlcv_ny_days,
        "qa_fixture_ohlcv_ny_days_separate": qa_ohlcv_ny_days,
        "dev_coverage_classification": cov_verdict,
        "ohlcv_summary": {
            "evidence_class": ohlcv_m.get("evidence_class", "MISSING"),
            "normalized_unique_bars_count": int(ohlcv_m.get("normalized_unique_bars_count", 0)),
            "gap_intervals_count": int(ohlcv_m.get("gap_intervals_count", 0)),
            "missing_bars_internal_gaps": int(ohlcv_m.get("missing_bars_internal_gaps", 0)),
            "coverage_ratio_in_requested_range": ohlcv_m.get("coverage_ratio_in_requested_range", 0.0),
        },
        "bbo_summary": {
            "evidence_class": bbo_m.get("evidence_class", "MISSING"),
            "bbo_coverage_classification": bbo_m.get("bbo_coverage_classification", "MISSING"),
            "valid_unique_timestamp_quotes_count": int(bbo_m.get("valid_unique_timestamp_quotes_count", 0)),
            "grid_coverage_ratio": bbo_m.get("grid_coverage_ratio", 0.0),
            "max_gap_ms": bbo_m.get("max_gap_ms", 0),
            "strategy_decision_fill_coverage": bbo_m.get("strategy_decision_fill_coverage", "UNKNOWN_NO_AUTHORIZED_ALIGNMENT_PROTOCOL"),
        },
        "funding_summary": {
            "evidence_class": fund_m.get("evidence_class", "MISSING"),
            "funding_readiness": fund_m.get("funding_readiness", "MISSING"),
            "settled_records_count": int(fund_m.get("settled_records_count", 0)),
            "missing_settlements_count": int(fund_m.get("missing_settlements_count", 0)),
        },
        "calendar_days_not_independent_trials_note": "Calendar/NY days are descriptive regime span only, never proof of statistical power.",
        "status": "PASS",
    }


# =====================================================================
# 10. build_r5_execution_fidelity_register
# =====================================================================
def build_r5_execution_fidelity_register(
    metadata: Mapping[str, Any],
    bbo: Mapping[str, Any],
    funding: Mapping[str, Any],
    fee_status: Mapping[str, Any],
    coverage: Mapping[str, Any],
    legacy_register: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Produces row-by-row execution fidelity register classifying every domain strictly into
    `{OBSERVED_VERIFIED, OWNER_ATTESTED, DERIVED_FROM_OBSERVED, MODELED, QA_FIXTURE, MISSING, UNVERIFIED}`.
    Separates historical R4 canonical DEV (REUSED_DEV) from new DEV observed readiness.
    """
    rows: List[Dict[str, Any]] = [
        {
            "domain": "historical_reused_dev_ohlcv_canonical",
            "required": True,
            "evidence_class": "OBSERVED_VERIFIED",
            "evidence_sha256": CANONICAL_DATASET_SHA256_KH02,
            "latest_effective_date": "2026-10-28T14:00:00Z",
            "blockers": ["REUSED_DEV_EXHAUSTED_DO_NOT_TUNE_OR_USE_AS_OOS"],
            "can_infer": "Deterministic regression parity and historical Decimal P&L reconciliation only.",
            "cannot_infer": "Out-of-sample alpha, live fills, or real L1 bid/ask spreads.",
        },
        {
            "domain": "live_exchange_instrument_metadata_and_stop_trigger_rules",
            "required": True,
            "evidence_class": metadata.get("evidence_class", "MISSING"),
            "evidence_sha256": metadata.get("raw_sha256"),
            "latest_effective_date": metadata.get("normalized_metadata", {}).get("effective_timestamp_ms"),
            "blockers": [] if metadata.get("evidence_class") == "OBSERVED_VERIFIED" else ["CAPTURE_BLOCKED_OWNER_AUTHORIZATION_OR_QA_ONLY"],
            "can_infer": "Parser & contract precision validation contract (QA offline)" if metadata.get("evidence_class") != "OBSERVED_VERIFIED" else "Observed contract tick/lot rules.",
            "cannot_infer": "Production sizing rules or unverified stop-trigger mark vs last price behavior.",
        },
        {
            "domain": "observed_l1_bbo_bid_ask_spread_dynamics",
            "required": True,
            "evidence_class": bbo.get("evidence_class", "MISSING"),
            "evidence_sha256": bbo.get("canonical_normalized_sha256"),
            "latest_effective_date": None,
            "blockers": ["CAPTURE_BLOCKED_OWNER_AUTHORIZATION", "BLOCKED_CONTINUOUS_BBO_REST_SNAPSHOT_ONLY"],
            "can_infer": "Modeled $0.20 spread used in legacy DEV replay only; QA parser tested offline.",
            "cannot_infer": "Real orderbook depth, fill slippage, or strategy decision/fill coverage.",
        },
        {
            "domain": "exchange_account_effective_fee_schedule",
            "required": True,
            "evidence_class": fee_status.get("evidence_class", "MISSING"),
            "evidence_sha256": fee_status.get("redacted_digest_sha256"),
            "latest_effective_date": fee_status.get("effective_date_utc"),
            "blockers": [] if fee_status.get("account_fee_readiness") in ("OWNER_ATTESTED", "VERIFIED") else ["UNKNOWN_NOT_LOCALLY_VERIFIED"],
            "can_infer": "Modeled 0.04% taker fee sensitivity only (non-account-verified).",
            "cannot_infer": "Owner's actual VIP/rebate/regional fee tier without redacted owner confirmation.",
        },
        {
            "domain": "perpetual_funding_rate_settlement_history",
            "required": True,
            "evidence_class": funding.get("evidence_class", "MISSING"),
            "evidence_sha256": funding.get("canonical_normalized_sha256"),
            "latest_effective_date": None,
            "blockers": ["UNSUPPORTED_OR_UNVERIFIED_PUBLIC_FUNDING_ENDPOINT", "CAPTURE_BLOCKED_OWNER_AUTHORIZATION"],
            "can_infer": "Funding arithmetic & schedule completeness checks (QA offline); legacy R4 remains EXCLUDED_UNMODELED.",
            "cannot_infer": "Actual historical carrying cost or adjusted R4 net P&L.",
        },
        {
            "domain": "observed_execution_slippage_and_maker_taker_fill_mix",
            "required": True,
            "evidence_class": "MISSING",
            "evidence_sha256": None,
            "latest_effective_date": None,
            "blockers": ["NO_AUTHORIZED_REDACTED_FILL_EVIDENCE_SUPPLIED"],
            "can_infer": "Modeled $0.10 slippage sensitivity only.",
            "cannot_infer": "Real fill slippage, partial fills, or live tail loss beyond $2.50 planned cap.",
        },
        {
            "domain": "expanded_dev_market_data_coverage_60_ny_days",
            "required": True,
            "evidence_class": "OBSERVED_VERIFIED" if coverage.get("observed_verified_ohlcv_ny_days", 0) >= 60 else "MISSING",
            "evidence_sha256": None,
            "latest_effective_date": None,
            "blockers": ["INSUFFICIENT_OBSERVED_DEV_COVERAGE_ZERO_NEW_REAL_CAPTURE"],
            "can_infer": "Offline coverage & NY-day DST calculator verified.",
            "cannot_infer": "Cross-regime generalization or statistical power.",
        },
        {
            "domain": "fresh_untouched_prospective_oos_window",
            "required": True,
            "evidence_class": "MISSING",
            "evidence_sha256": None,
            "latest_effective_date": None,
            "blockers": ["BLOCKED_NO_UNTOUCHED_DATA", "BLOCKED_NO_DEV_CANDIDATE"],
            "can_infer": "Deny-before-open escrow isolation & OOS_EVALUATIONS=0 enforced.",
            "cannot_infer": "Out-of-sample viability; new DEV capture is NEVER OOS.",
        },
    ]

    non_observed_required = [r["domain"] for r in rows if r["required"] and r["evidence_class"] not in ("OBSERVED_VERIFIED", "OWNER_ATTESTED")]
    return {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "allowed_evidence_classes": list(EVIDENCE_CLASSES_R5),
        "legacy_r4_register_linked": bool(legacy_register),
        "execution_fidelity_status": "INCOMPLETE" if non_observed_required else "EVIDENCE_READY_FOR_SEPARATE_REVIEW",
        "incomplete_required_domains_count": len(non_observed_required),
        "incomplete_required_domains": non_observed_required,
        "domains": rows,
        "next_owner_action": (
            "Decide between (A) pausing research completely, or (B) issuing an explicit separate bounded public DEV capture authorization "
            "(symbol, product_type, public GET endpoints, max requests/bytes/duration, local data root) plus a redacted owner fee confirmation "
            "(NO API keys, secrets, or passwords). Never activate trading or tune reused Oct-2026 DEV."
        ),
        "status": "PASS",
    }
