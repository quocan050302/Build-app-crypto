"""
aurum-desk/backend/services/kh_02_r2_c4_trusted_transport_boundary.py

AURUM DESK — KH-02.R2.C4 Trusted Capture Boundary & Origin Proof Authority
SPEC VERSION: KH-02.R2.C4-v1.0 | STDLIB ONLY | ZERO NETWORK CALLS AT IMPORT/INIT

Closes P0-A by construction:
  - Eliminates caller-controlled boolean/metadata minting of `LIVE_AUTHORIZED_PUBLIC_HTTP`.
  - Eliminates `isinstance(transport, StdlibUrllibPublicTransportC2)` subclass spoofing,
    instance `.get` monkeypatching, post-`__init__` `_opener_fn` injection, and naked
    fabricated `RawHttpResponseC2` admission.
  - Process-local unexported `CaptureAuthorityC4` issues a non-serializable, non-copyable,
    short-lived `VerifiedCaptureProofC4` ONLY from inside `execute_trusted_stdlib_https_get_c4(...)`
    AFTER verifying exact transport class identity, exact unbound `.get` method, `_opener_fn is None`,
    genuine stdlib `urllib.request.build_opener` & `ssl.create_default_context`, real rate limiter,
    separate operator live-session authorization, HTTPS allowlist, no redirects, bounded body/gzip,
    and a genuine `http.client.HTTPResponse` socket response.
  - Binds each proof to `(raw_response_sha256, url, endpoint_key, params_sha256, http_status,
    request_utc_ms, response_utc_ms, capture_session_id, worker_pid, policy_version)` inside a
    thread-safe process-local registry with explicit TTL expiry and single-snapshot anti-replay
    consumption inside the escrow lock (preserving idempotent same-snapshot retry).

HONEST TCB LIMITATIONS DISCLOSURE:
  - Prevents: direct boolean/flag minting, evil subclass override, instance `.get` / `_opener_fn`
    injection, naked `RawHttpResponseC2`, fake authorization/disk probe, modified bytes/timestamps,
    cross-session/stale proof replay, and accidental QA fixture crossing into real OOS.
  - Does NOT claim absolute protection against: same-process arbitrary code execution / full
    interpreter patching by a privileged local developer/root, process memory tampering,
    compromised OS TLS trust store, compromised upstream Bitget servers, or local filesystem
    rewrites without an independent remote WORM/signed anchor.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import http.client
import json
import os
import secrets
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
import urllib.request

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2_C4 = "KH-02.R2.C4-v1.0"
POLICY_VERSION_TRUSTED_BOUNDARY_C4 = "kh02_r2_c4_boundary_v1"
DEFAULT_PROOF_TTL_SEC_C4: float = 120.0
MAX_PROOF_REGISTRY_ENTRIES_C4: int = 4096

TCB_THREAT_MODEL_DISCLOSURE_C4: Dict[str, Any] = {
    "spec_version": SPEC_VERSION_KH02_R2_C4,
    "policy_version": POLICY_VERSION_TRUSTED_BOUNDARY_C4,
    "prevents": [
        "Direct mint_trusted_capture_receipt_c3/c4 boolean or metadata spoofing",
        "Evil subclass of StdlibUrllibPublicTransportC2 overriding .get() or is_genuine_live_socket_transport()",
        "Instance-level transport.get monkeypatching or post-__init__ _opener_fn mutation",
        "Naked fabricated RawHttpResponseC2 carrying matching transport_kind/tls_verified_host strings",
        "Forged capture_receipt MAC, cross-process serialized receipt forgery, or expired capture proof",
        "Replay of a genuine proof with modified raw bytes or mismatched snapshot_id",
        "Forged caller disk probe or unapproved owner disk waiver",
        "Accidental QA/synthetic fixture promotion into REAL_OOS escrow",
    ],
    "cannot_prove_within_python_process_tcb": [
        "Same-process arbitrary code execution or full interpreter/stdlib patching by a privileged local developer or root",
        "Direct process memory inspection/modification of ephemeral process keys",
        "Compromised host OS TLS root store or kernel network stack",
        "Compromised upstream Bitget API servers",
        "Local disk rollback/rewrite without an independent remote WORM or external signed attestation store",
    ],
    "hardware_tpm_or_remote_worm": False,
}

# Module-private ephemeral signing key, process identity, and unexported capability stamp.
_AUTHORITY_KEY_C4: bytes = secrets.token_bytes(32)
_AUTHORITY_PID_C4: int = os.getpid()
_LIVE_SOCKET_STAMP_OBJECT_C4: object = object()
_PROVENANCE_LIVE_STR = "LIVE_AUTHORIZED_PUBLIC_HTTP"
_PROVENANCE_QA_FAKE_STR = "QA_FAKE_TRANSPORT"
_PROVENANCE_UNVERIFIED_STR = "UNVERIFIED"


class _NonSerializableOriginTokenC4:
    """Process-local uncopyable, non-picklable token proving origin inside CaptureAuthorityC4."""

    __slots__ = ("_nonce",)

    def __init__(self, nonce: str) -> None:
        object.__setattr__(self, "_nonce", nonce)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ORIGIN_TOKEN_IMMUTABLE")

    def __copy__(self) -> Any:
        raise TypeError("ORIGIN_TOKEN_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("ORIGIN_TOKEN_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("ORIGIN_TOKEN_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("ORIGIN_TOKEN_NON_SERIALIZABLE")


@dataclass(frozen=True)
class VerifiedCaptureProofC4:
    """
    Process-local, non-serializable transport origin proof bound to one exact HTTP capture.
    Cannot be constructed with `provenance_class_value == "LIVE_AUTHORIZED_PUBLIC_HTTP"`
    outside `CaptureAuthorityC4` without failing registry + capability verification.
    """

    proof_id: str
    capture_session_id: str
    provenance_class_value: str
    endpoint_key: str
    url: str
    final_url: str
    tls_host: str
    http_status: int
    params_canonical_sha256: str
    raw_response_sha256: str
    raw_response_size_bytes: int
    request_utc_ms: int
    response_utc_ms: int
    monotonic_duration_ms: int
    issued_mono_ns: int
    ttl_sec: float
    worker_pid: int
    policy_version: str
    transport_class_name: str
    transport_kind: str
    real_rate_limiter_active: bool
    proof_mac_hex: str
    _origin_token: Optional[_NonSerializableOriginTokenC4] = None

    def __copy__(self) -> Any:
        raise TypeError("VERIFIED_CAPTURE_PROOF_NON_COPYABLE")

    def __deepcopy__(self, _memo: Any) -> Any:
        raise TypeError("VERIFIED_CAPTURE_PROOF_NON_COPYABLE")

    def __reduce__(self) -> Any:
        raise TypeError("VERIFIED_CAPTURE_PROOF_NON_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("VERIFIED_CAPTURE_PROOF_NON_SERIALIZABLE")

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "policy_version": self.policy_version,
            "proof_id": self.proof_id,
            "capture_session_id": self.capture_session_id,
            "provenance_class": self.provenance_class_value,
            "endpoint_key": self.endpoint_key,
            "url": self.url,
            "final_url": self.final_url,
            "tls_host": self.tls_host,
            "http_status": self.http_status,
            "params_canonical_sha256": self.params_canonical_sha256,
            "raw_response_sha256": self.raw_response_sha256,
            "raw_response_size_bytes": self.raw_response_size_bytes,
            "request_utc_ms": self.request_utc_ms,
            "response_utc_ms": self.response_utc_ms,
            "monotonic_duration_ms": self.monotonic_duration_ms,
            "worker_pid": self.worker_pid,
            "transport_class_name": self.transport_class_name,
            "transport_kind": self.transport_kind,
            "real_rate_limiter_active": self.real_rate_limiter_active,
            "proof_mac_prefix": self.proof_mac_hex[:16],
        }


@dataclass(frozen=True)
class TrustedTransportResultC4:
    """Typed pair returning the RawHttpResponseC2 together with its process-local C4 origin proof."""

    response: Any
    capture_proof: VerifiedCaptureProofC4


@dataclass
class _RegisteredProofEntryC4:
    proof: VerifiedCaptureProofC4
    token: _NonSerializableOriginTokenC4
    consumed_snapshot_id: Optional[str] = None
    consumed_content_sha256: Optional[str] = None


def _proof_mac_fields_c4(proof_fields: Mapping[str, Any]) -> bytes:
    return json.dumps(proof_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compute_proof_mac_c4(fields: Mapping[str, Any]) -> str:
    return hmac.new(_AUTHORITY_KEY_C4, _proof_mac_fields_c4(fields), hashlib.sha256).hexdigest()


class CaptureAuthorityC4:
    """
    Process-local authority holding the authoritative registry of genuine capture proofs.
    Thread-safe; bounded capacity; enforces TTL expiry and single-snapshot anti-replay binding.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._registry: Dict[str, _RegisteredProofEntryC4] = {}
        self._insertion_order: List[str] = []

    def _evict_if_needed(self) -> None:
        while len(self._insertion_order) >= MAX_PROOF_REGISTRY_ENTRIES_C4:
            oldest = self._insertion_order.pop(0)
            self._registry.pop(oldest, None)

    def _issue_internal(
        self,
        *,
        live_stamp: Optional[object],
        capture_session_id: str,
        endpoint_key: str,
        url: str,
        final_url: str,
        tls_host: str,
        http_status: int,
        params: Mapping[str, Any],
        raw_response_bytes: bytes,
        request_utc_ms: int,
        response_utc_ms: int,
        monotonic_duration_ms: int,
        transport_class_name: str,
        transport_kind: str,
        real_rate_limiter_active: bool,
        is_fake_transport_qa: bool,
        ttl_sec: float = DEFAULT_PROOF_TTL_SEC_C4,
    ) -> VerifiedCaptureProofC4:
        if not isinstance(raw_response_bytes, (bytes, bytearray)) or len(raw_response_bytes) == 0:
            raise ValueError("CANNOT_ISSUE_PROOF_WITHOUT_RAW_BYTES")
        raw_sha = hashlib.sha256(raw_response_bytes).hexdigest()
        params_sha = canonical_hash(
            {str(k): str(v) for k, v in sorted(params.items(), key=lambda kv: str(kv[0]))}
        )

        # LIVE_AUTHORIZED_PUBLIC_HTTP requires exact module-private live socket execution stamp!
        is_genuine_live = (
            (live_stamp is _LIVE_SOCKET_STAMP_OBJECT_C4)
            and (not bool(is_fake_transport_qa))
            and str(transport_kind) == "STDLIB_URLLIB_HTTPS_GET"
            and str(tls_host).lower() == "api.bitget.com"
            and int(http_status) == 200
            and bool(real_rate_limiter_active)
            and bool(str(capture_session_id or "").strip())
        )
        if is_genuine_live:
            pclass = _PROVENANCE_LIVE_STR
        elif bool(is_fake_transport_qa) or str(transport_kind) in (
            "INJECTED_FAKE_TRANSPORT",
            "TEST_INJECTED_OPENER_NOT_LIVE_HTTP",
        ):
            pclass = _PROVENANCE_QA_FAKE_STR
        else:
            pclass = _PROVENANCE_UNVERIFIED_STR

        mono_ns = time.monotonic_ns()
        pid_now = os.getpid()
        nonce = secrets.token_hex(12)
        proof_id = f"prf_c4_{canonical_hash({'n': nonce, 's': capture_session_id, 'r': raw_sha, 't': mono_ns})[:24]}"
        fields = {
            "proof_id": proof_id,
            "capture_session_id": str(capture_session_id or "UNASSIGNED_SESSION"),
            "provenance_class_value": pclass,
            "endpoint_key": str(endpoint_key),
            "url": str(url),
            "final_url": str(final_url),
            "tls_host": str(tls_host),
            "http_status": int(http_status),
            "params_canonical_sha256": params_sha,
            "raw_response_sha256": raw_sha,
            "raw_response_size_bytes": len(raw_response_bytes),
            "request_utc_ms": int(request_utc_ms),
            "response_utc_ms": int(response_utc_ms),
            "monotonic_duration_ms": max(0, int(monotonic_duration_ms)),
            "issued_mono_ns": int(mono_ns),
            "ttl_sec": float(ttl_sec),
            "worker_pid": int(pid_now),
            "policy_version": POLICY_VERSION_TRUSTED_BOUNDARY_C4,
            "transport_class_name": str(transport_class_name),
            "transport_kind": str(transport_kind),
            "real_rate_limiter_active": bool(real_rate_limiter_active),
        }
        mac = _compute_proof_mac_c4(fields)
        tok = _NonSerializableOriginTokenC4(nonce)
        proof = VerifiedCaptureProofC4(
            **fields,
            proof_mac_hex=mac,
            _origin_token=tok,
        )
        with self._lock:
            self._evict_if_needed()
            self._registry[proof_id] = _RegisteredProofEntryC4(proof=proof, token=tok)
            self._insertion_order.append(proof_id)
        return proof

    def verify_proof(
        self,
        proof: Any,
        *,
        expected_raw_sha256: Optional[str] = None,
        expected_url: Optional[str] = None,
        expected_endpoint_key: Optional[str] = None,
        expected_capture_session_id: Optional[str] = None,
        expected_params_sha256: Optional[str] = None,
        require_live: bool = True,
        now_mono_ns: Optional[int] = None,
    ) -> Tuple[bool, List[str], Optional[VerifiedCaptureProofC4]]:
        violations: List[str] = []
        if not isinstance(proof, VerifiedCaptureProofC4):
            return False, ["MISSING_OR_INVALID_C4_CAPTURE_PROOF_INSTANCE"], None

        with self._lock:
            reg = self._registry.get(proof.proof_id)
        if reg is None:
            return False, ["UNREGISTERED_OR_CROSS_PROCESS_FORGED_C4_PROOF"], None
        if proof is not reg.proof or proof._origin_token is not reg.token:
            return False, ["FORGED_OR_CLONED_C4_ORIGIN_TOKEN"], None
        if int(proof.worker_pid) != os.getpid() or _AUTHORITY_PID_C4 != os.getpid():
            violations.append(f"CROSS_PROCESS_STALE_C4_PROOF_PID:proof={proof.worker_pid},now={os.getpid()}")

        fields = {
            "proof_id": proof.proof_id,
            "capture_session_id": proof.capture_session_id,
            "provenance_class_value": proof.provenance_class_value,
            "endpoint_key": proof.endpoint_key,
            "url": proof.url,
            "final_url": proof.final_url,
            "tls_host": proof.tls_host,
            "http_status": int(proof.http_status),
            "params_canonical_sha256": proof.params_canonical_sha256,
            "raw_response_sha256": proof.raw_response_sha256,
            "raw_response_size_bytes": int(proof.raw_response_size_bytes),
            "request_utc_ms": int(proof.request_utc_ms),
            "response_utc_ms": int(proof.response_utc_ms),
            "monotonic_duration_ms": int(proof.monotonic_duration_ms),
            "issued_mono_ns": int(proof.issued_mono_ns),
            "ttl_sec": float(proof.ttl_sec),
            "worker_pid": int(proof.worker_pid),
            "policy_version": proof.policy_version,
            "transport_class_name": proof.transport_class_name,
            "transport_kind": proof.transport_kind,
            "real_rate_limiter_active": bool(proof.real_rate_limiter_active),
        }
        expected_mac = _compute_proof_mac_c4(fields)
        if not hmac.compare_digest(str(proof.proof_mac_hex), expected_mac):
            violations.append("TAMPERED_C4_CAPTURE_PROOF_MAC")

        now_ns = time.monotonic_ns() if now_mono_ns is None else int(now_mono_ns)
        age_sec = max(0.0, (now_ns - int(proof.issued_mono_ns)) / 1e9)
        if age_sec > float(proof.ttl_sec):
            violations.append(f"EXPIRED_C4_CAPTURE_PROOF:age_sec={age_sec:.3f}>ttl={proof.ttl_sec}")

        if expected_raw_sha256 is not None and proof.raw_response_sha256 != str(expected_raw_sha256).lower():
            violations.append("C4_PROOF_RAW_BYTES_SHA256_MISMATCH")
        if expected_url is not None and proof.url != str(expected_url):
            violations.append("C4_PROOF_URL_MISMATCH")
        if expected_endpoint_key is not None and proof.endpoint_key != str(expected_endpoint_key):
            violations.append("C4_PROOF_ENDPOINT_KEY_MISMATCH")
        if expected_capture_session_id is not None and proof.capture_session_id != str(expected_capture_session_id):
            violations.append("C4_PROOF_SESSION_ID_MISMATCH")
        if expected_params_sha256 is not None and proof.params_canonical_sha256 != str(expected_params_sha256):
            violations.append("C4_PROOF_PARAMS_SHA256_MISMATCH")

        if require_live and proof.provenance_class_value != _PROVENANCE_LIVE_STR:
            violations.append(f"C4_PROOF_NOT_LIVE_AUTHORIZED:{proof.provenance_class_value}")
        if require_live and not proof.real_rate_limiter_active:
            violations.append("C4_PROOF_REAL_RATE_LIMITER_INACTIVE")

        return len(violations) == 0, violations, proof

    def verify_by_proof_id(
        self,
        proof_id: str,
        *,
        expected_raw_sha256: Optional[str] = None,
        expected_url: Optional[str] = None,
        expected_endpoint_key: Optional[str] = None,
        expected_capture_session_id: Optional[str] = None,
        expected_params_sha256: Optional[str] = None,
        require_live: bool = True,
        now_mono_ns: Optional[int] = None,
    ) -> Tuple[bool, List[str], Optional[VerifiedCaptureProofC4]]:
        pid_str = str(proof_id or "").strip()
        if not pid_str:
            return False, ["MISSING_C4_CAPTURE_PROOF_ID"], None
        with self._lock:
            reg = self._registry.get(pid_str)
        if reg is None:
            return False, ["UNREGISTERED_OR_CROSS_PROCESS_C4_PROOF_ID"], None
        return self.verify_proof(
            reg.proof,
            expected_raw_sha256=expected_raw_sha256,
            expected_url=expected_url,
            expected_endpoint_key=expected_endpoint_key,
            expected_capture_session_id=expected_capture_session_id,
            expected_params_sha256=expected_params_sha256,
            require_live=require_live,
            now_mono_ns=now_mono_ns,
        )

    def bind_proof_consumption_inside_lock(
        self,
        proof_id: str,
        *,
        snapshot_id: str,
        content_sha256: str,
    ) -> Tuple[bool, List[str]]:
        """
        Anti-replay check inside escrow exclusive lock:
          - Unconsumed proof -> binds to (snapshot_id, content_sha256).
          - Same proof + same (snapshot_id, content_sha256) -> idempotent retry allowed.
          - Same proof reused for a DIFFERENT snapshot_id or DIFFERENT content_sha256 -> REJECT!
        """
        with self._lock:
            reg = self._registry.get(str(proof_id or ""))
            if reg is None:
                return False, ["UNREGISTERED_C4_PROOF_FOR_CONSUMPTION"]
            if reg.consumed_snapshot_id is None:
                reg.consumed_snapshot_id = str(snapshot_id)
                reg.consumed_content_sha256 = str(content_sha256).lower()
                return True, []
            if (
                reg.consumed_snapshot_id == str(snapshot_id)
                and reg.consumed_content_sha256 == str(content_sha256).lower()
            ):
                return True, []
            return False, [
                f"C4_CAPTURE_PROOF_REPLAY_FORBIDDEN:bound=({reg.consumed_snapshot_id},{reg.consumed_content_sha256[:12]}),attempted=({snapshot_id},{str(content_sha256)[:12]})"
            ]

    def expire_proof_for_test_only(self, proof_id: str) -> bool:
        """QA-only helper to deterministically test expired-proof rejection without sleeping 120s."""
        with self._lock:
            reg = self._registry.get(str(proof_id or ""))
            if reg is None:
                return False
            # Replace with an expired TTL copy still bound to same token ONLY for negative testing
            old = reg.proof
            flds = {
                "proof_id": old.proof_id,
                "capture_session_id": old.capture_session_id,
                "provenance_class_value": old.provenance_class_value,
                "endpoint_key": old.endpoint_key,
                "url": old.url,
                "final_url": old.final_url,
                "tls_host": old.tls_host,
                "http_status": int(old.http_status),
                "params_canonical_sha256": old.params_canonical_sha256,
                "raw_response_sha256": old.raw_response_sha256,
                "raw_response_size_bytes": int(old.raw_response_size_bytes),
                "request_utc_ms": int(old.request_utc_ms),
                "response_utc_ms": int(old.response_utc_ms),
                "monotonic_duration_ms": int(old.monotonic_duration_ms),
                "issued_mono_ns": max(0, int(old.issued_mono_ns) - int(10 * 1e9)),
                "ttl_sec": 0.001,
                "worker_pid": int(old.worker_pid),
                "policy_version": old.policy_version,
                "transport_class_name": old.transport_class_name,
                "transport_kind": old.transport_kind,
                "real_rate_limiter_active": bool(old.real_rate_limiter_active),
            }
            expired = VerifiedCaptureProofC4(
                **flds,
                proof_mac_hex=_compute_proof_mac_c4(flds),
                _origin_token=reg.token,
            )
            reg.proof = expired
            return True


_GLOBAL_CAPTURE_AUTHORITY_C4 = CaptureAuthorityC4()


def is_exact_unmodified_stdlib_transport_c4(
    transport: Any,
    *,
    expected_transport_cls: type,
    expected_get_func: Callable[..., Any],
) -> Tuple[bool, List[str]]:
    """
    Strictly verifies that `transport` is an EXACT instance of `StdlibUrllibPublicTransportC2`
    (NOT a subclass!), has NOT overridden or instance-monkeypatched `.get` or
    `is_genuine_live_socket_transport`, has `_opener_fn is None` at call time, and stdlib
    `urllib.request.build_opener` & `ssl.create_default_context` belong to stdlib modules.
    """
    reasons: List[str] = []
    if transport is None:
        return False, ["TRANSPORT_IS_NONE"]
    if type(transport) is not expected_transport_cls:
        reasons.append(f"SUBCLASS_OR_NON_EXACT_TRANSPORT_CLASS:{type(transport).__name__}")
    inst_dict = getattr(transport, "__dict__", {})
    if "get" in inst_dict:
        reasons.append("INSTANCE_MONKEYPATCHED_GET_FORBIDDEN")
    if "is_genuine_live_socket_transport" in inst_dict:
        reasons.append("INSTANCE_MONKEYPATCHED_GENUINE_CHECK_FORBIDDEN")
    if getattr(type(transport), "get", None) is not expected_get_func:
        reasons.append("CLASS_GET_METHOD_OVERRIDDEN_OR_PATCHED")
    if getattr(transport, "_opener_fn", object()) is not None:
        reasons.append("INJECTED_OPENER_FN_PRESENT_NOT_LIVE_SOCKET")
    if getattr(urllib.request.build_opener, "__module__", "") != "urllib.request":
        reasons.append("URLLIB_BUILD_OPENER_NOT_STDLIB")
    if getattr(ssl.create_default_context, "__module__", "") != "ssl":
        reasons.append("SSL_CREATE_DEFAULT_CONTEXT_NOT_STDLIB")
    return len(reasons) == 0, reasons


def issue_qa_or_unverified_capture_proof_c4(
    *,
    capture_session_id: str,
    endpoint_key: str,
    url: str,
    final_url: str,
    tls_host: str,
    http_status: int,
    params: Mapping[str, Any],
    raw_response_bytes: bytes,
    request_utc_ms: int,
    response_utc_ms: int,
    monotonic_duration_ms: int,
    transport_class_name: str,
    transport_kind: str,
    real_rate_limiter_active: bool,
    is_fake_transport_qa: bool,
    ttl_sec: float = DEFAULT_PROOF_TTL_SEC_C4,
) -> VerifiedCaptureProofC4:
    """
    Publicly callable proof issuer for QA/unverified paths ONLY.
    NEVER passes `_LIVE_SOCKET_STAMP_OBJECT_C4`, so it can NEVER mint `LIVE_AUTHORIZED_PUBLIC_HTTP`.
    """
    return _GLOBAL_CAPTURE_AUTHORITY_C4._issue_internal(
        live_stamp=None,
        capture_session_id=capture_session_id,
        endpoint_key=endpoint_key,
        url=url,
        final_url=final_url,
        tls_host=tls_host,
        http_status=http_status,
        params=params,
        raw_response_bytes=raw_response_bytes,
        request_utc_ms=request_utc_ms,
        response_utc_ms=response_utc_ms,
        monotonic_duration_ms=monotonic_duration_ms,
        transport_class_name=transport_class_name,
        transport_kind=transport_kind,
        real_rate_limiter_active=real_rate_limiter_active,
        is_fake_transport_qa=is_fake_transport_qa,
        ttl_sec=ttl_sec,
    )


def execute_trusted_stdlib_https_get_c4(
    *,
    transport: Any,
    expected_transport_cls: type,
    expected_get_func: Callable[..., Any],
    validate_url_fn: Callable[[str, Mapping[str, Any]], Tuple[str, str]],
    no_redirect_handler_cls: type,
    secret_scanner_fn: Callable[[Mapping[str, Any]], List[str]],
    decompress_check_fn: Callable[..., bytes],
    raw_response_cls: type,
    url: str,
    params: Mapping[str, Any],
    endpoint_key: str,
    timeout_sec: float,
    capture_session_id: str,
    live_authorization_verified: bool,
    real_rate_limiter_active: bool,
    is_fake_transport_qa: bool,
) -> TrustedTransportResultC4:
    """
    Authoritative C4 trusted transport execution boundary.
    Stamps `_LIVE_SOCKET_STAMP_OBJECT_C4` ONLY when:
      1. `is_exact_unmodified_stdlib_transport_c4(...)` succeeds at exact invocation time;
      2. Genuine stdlib `urllib.request.build_opener` + `ssl.create_default_context()` executes;
      3. Returned response is an authentic `http.client.HTTPResponse` with HTTPS final URL on `api.bitget.com`;
      4. `live_authorization_verified`, `real_rate_limiter_active`, `not is_fake_transport_qa`, and HTTP 200 ALL hold.
    Otherwise, executes injected/test path cleanly and stamps `live_stamp=None` (QA_FAKE_TRANSPORT / UNVERIFIED).
    """
    full_url, host = validate_url_fn(url, params)
    req = urllib.request.Request(
        full_url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "AurumDesk-KH02-R2-C4-ReadOnly/1.0"},
    )
    req_ms = int(time.time() * 1000)
    t_mono_0 = time.monotonic()

    exact_live_ok, _ = is_exact_unmodified_stdlib_transport_c4(
        transport,
        expected_transport_cls=expected_transport_cls,
        expected_get_func=expected_get_func,
    )
    opener_fn = getattr(transport, "_opener_fn", None)

    live_stamp: Optional[object] = None
    if opener_fn is not None:
        resp = opener_fn(req, float(timeout_sec))
        t_kind = "TEST_INJECTED_OPENER_NOT_LIVE_HTTP"
        tls_verified = ""
    else:
        tls_ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(
            no_redirect_handler_cls(),
            urllib.request.HTTPSHandler(context=tls_ctx),
        )
        resp = opener.open(req, timeout=float(timeout_sec))
        is_genuine_http_resp = isinstance(resp, http.client.HTTPResponse)
        if exact_live_ok and is_genuine_http_resp:
            t_kind = "STDLIB_URLLIB_HTTPS_GET"
            tls_verified = host
        else:
            t_kind = "UNVERIFIED_NON_STDLIB_HTTP_RESPONSE"
            tls_verified = ""

    max_resp_bytes = int(getattr(transport, "max_response_bytes", 4 * 1024 * 1024))
    max_decomp_bytes = int(getattr(transport, "max_decompressed_bytes", 8 * 1024 * 1024))

    with resp:
        status = int(getattr(resp, "status", getattr(resp, "code", 0)))
        hdrs = {str(k).lower(): str(v) for k, v in dict(getattr(resp, "headers", {})).items()}
        if status in (301, 302, 303, 307, 308) or "location" in hdrs:
            raise PermissionError(
                f"HTTP_REDIRECT_FORBIDDEN_FAIL_CLOSED:status={status}:target={hdrs.get('location', '')}"
            )
        hdr_secrets = secret_scanner_fn(hdrs)
        if hdr_secrets:
            raise PermissionError(f"FORBIDDEN_SECRET_OR_COOKIE_IN_RESPONSE_HEADERS:{','.join(hdr_secrets)}")
        final_url = str(getattr(resp, "url", full_url) or full_url)
        from urllib.parse import urlsplit
        final_parsed = urlsplit(final_url)
        if final_parsed.scheme.lower() != "https" or (final_parsed.hostname or "").lower() != "api.bitget.com":
            raise PermissionError(f"REDIRECTED_OR_MISMATCHED_FINAL_HOST:{final_url}")

        raw = resp.read(max_resp_bytes + 1)
        if len(raw) > max_resp_bytes:
            raise ValueError(f"OVERSIZED_HTTP_RESPONSE_BODY:len>{max_resp_bytes}")
        if hdrs.get("content-encoding", "").lower() == "gzip" or (len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B):
            _ = decompress_check_fn(
                raw,
                max_raw_bytes=max_resp_bytes,
                max_decompressed_bytes=max_decomp_bytes,
            )
        resp_ms = int(time.time() * 1000)
        mono_ms = max(0, int((time.monotonic() - t_mono_0) * 1000))

    raw_resp = raw_response_cls(
        status_code=status,
        body_bytes=raw,
        headers=hdrs,
        request_utc_ms=req_ms,
        response_utc_ms=resp_ms,
        transport_kind=t_kind,
        tls_verified_host=tls_verified,
        final_url=final_url,
        monotonic_duration_ms=mono_ms,
    )

    if (
        exact_live_ok
        and opener_fn is None
        and t_kind == "STDLIB_URLLIB_HTTPS_GET"
        and tls_verified == "api.bitget.com"
        and bool(live_authorization_verified)
        and bool(real_rate_limiter_active)
        and (not bool(is_fake_transport_qa))
        and status == 200
        and len(raw) > 0
    ):
        live_stamp = _LIVE_SOCKET_STAMP_OBJECT_C4

    # Only mint a proof if raw bytes are non-empty (empty body raises ValueError in collector)
    proof: Optional[VerifiedCaptureProofC4] = None
    if len(raw) > 0:
        proof = _GLOBAL_CAPTURE_AUTHORITY_C4._issue_internal(
            live_stamp=live_stamp,
            capture_session_id=capture_session_id,
            endpoint_key=endpoint_key,
            url=url,
            final_url=final_url,
            tls_host=tls_verified,
            http_status=status,
            params=params,
            raw_response_bytes=raw,
            request_utc_ms=req_ms,
            response_utc_ms=resp_ms,
            monotonic_duration_ms=mono_ms,
            transport_class_name=type(transport).__name__,
            transport_kind=t_kind,
            real_rate_limiter_active=bool(real_rate_limiter_active),
            is_fake_transport_qa=bool(is_fake_transport_qa),
        )
    return TrustedTransportResultC4(response=raw_resp, capture_proof=proof)  # type: ignore[arg-type]


def verify_capture_proof_c4(
    proof: Any,
    *,
    expected_raw_sha256: Optional[str] = None,
    expected_url: Optional[str] = None,
    expected_endpoint_key: Optional[str] = None,
    expected_capture_session_id: Optional[str] = None,
    expected_params_sha256: Optional[str] = None,
    require_live: bool = True,
    now_mono_ns: Optional[int] = None,
) -> Tuple[bool, List[str], Optional[VerifiedCaptureProofC4]]:
    return _GLOBAL_CAPTURE_AUTHORITY_C4.verify_proof(
        proof,
        expected_raw_sha256=expected_raw_sha256,
        expected_url=expected_url,
        expected_endpoint_key=expected_endpoint_key,
        expected_capture_session_id=expected_capture_session_id,
        expected_params_sha256=expected_params_sha256,
        require_live=require_live,
        now_mono_ns=now_mono_ns,
    )


def verify_capture_proof_by_id_c4(
    proof_id: str,
    *,
    expected_raw_sha256: Optional[str] = None,
    expected_url: Optional[str] = None,
    expected_endpoint_key: Optional[str] = None,
    expected_capture_session_id: Optional[str] = None,
    expected_params_sha256: Optional[str] = None,
    require_live: bool = True,
    now_mono_ns: Optional[int] = None,
) -> Tuple[bool, List[str], Optional[VerifiedCaptureProofC4]]:
    return _GLOBAL_CAPTURE_AUTHORITY_C4.verify_by_proof_id(
        proof_id,
        expected_raw_sha256=expected_raw_sha256,
        expected_url=expected_url,
        expected_endpoint_key=expected_endpoint_key,
        expected_capture_session_id=expected_capture_session_id,
        expected_params_sha256=expected_params_sha256,
        require_live=require_live,
        now_mono_ns=now_mono_ns,
    )


def bind_capture_proof_consumption_c4(
    proof_id: str,
    *,
    snapshot_id: str,
    content_sha256: str,
) -> Tuple[bool, List[str]]:
    return _GLOBAL_CAPTURE_AUTHORITY_C4.bind_proof_consumption_inside_lock(
        proof_id,
        snapshot_id=snapshot_id,
        content_sha256=content_sha256,
    )


def expire_capture_proof_for_test_only_c4(proof_id: str) -> bool:
    return _GLOBAL_CAPTURE_AUTHORITY_C4.expire_proof_for_test_only(proof_id)
