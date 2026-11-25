"""
aurum-desk/backend/services/kh_03_r6_durable_grant_service.py

AURUM DESK — KH-03.R6 (M2) | DURABLE ONE-SHOT OWNER AUTHORIZATION & CROSS-PROCESS LEDGER
SPEC VERSION: KH-03.R6-v1.0 | STDLIB SQLITE + EXTERNAL ANCHOR + CRYPTOGRAPHY ED25519

Implements:
  - F05: load_r6_owner_trust_root(configured_path, ...) + provision_qa_isolated_trust_root(...)
  - F06: verify_r6_owner_grant(raw_grant, trust_root, actual_now, ...)
  - F07: canonical_r6_grant_identity(grant_payload, trust_namespace)
  - F08: provision_r6_durable_ledger(...) + open_r6_durable_ledger(owner_config, expected_store_id, ...)
  - F09: claim_r6_grant_session_atomic(verified_grant, verified_plan_digest, session_id, ...)
  - F10: reserve_r6_io_budget_atomic(lease, attempt_id, request_type, caps, ...)
  - F11: mark_r6_attempt_started / settle_r6_io_budget / finalize_r6_session
  - F12: recover_r6_ledger_after_restart / verify_r6_ledger_readonly

Threat Model Disclosure:
  - Mitigates: cross-process & cross-restart grant replay, plan/payload drift, budget overflow,
    double settle/release, crash partial-byte auto-refund, SQLite missing/replaced/corrupt/rolled-back
    store resets, clock rollback, unsigned/forged/QA-promoted approvals.
  - Does NOT claim defense against a fully compromised OS administrator/root or stolen owner private key.
  - Owner private signing material NEVER enters app runtime, logs, CLI flags, chat, or Git.
  - Uses installed `cryptography` Ed25519 verification (NO custom cryptography, NO embedded default owner key).
"""
from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)

OWNER_EXTERNAL_TRUST_NAMESPACE_R6 = "OWNER_EXTERNAL_PRODUCTION_TRUST"
QA_ISOLATED_TRUST_NAMESPACE_R6 = "QA_ISOLATED_TEST_TRUST"

LEDGER_SCHEMA_VERSION_R6 = "kh_03_r6_sqlite_ledger_v1"
GRANT_SCHEMA_VERSION_R6 = "kh_03_r6_owner_grant_v1"
TRUST_ROOT_SCHEMA_VERSION_R6 = "kh_03_r6_owner_trust_root_v1"

MAX_GRANT_JSON_BYTES_R6 = 65_536
MAX_CLOCK_SKEW_MS_R6 = 60_000
MAX_INT64_SAFE_R6 = (1 << 62) - 1
DEFAULT_LEDGER_MAX_GRANTS_R6 = 10_000
DEFAULT_LEDGER_MAX_BYTES_R6 = 64 * 1024 * 1024  # 64 MiB bounded store

ALLOWED_STAGES_EXTERNAL_R6: Tuple[str, ...] = ("B_SMOKE", "C_DEV")
ALLOWED_STAGES_QA_R6: Tuple[str, ...] = ("QA_LOOPBACK",)

BUDGET_FIELDS_R6: Tuple[str, ...] = (
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

FORBIDDEN_SECRET_KEY_TOKENS_R6: Tuple[str, ...] = (
    "api_key",
    "apikey",
    "api_secret",
    "apisecret",
    "secret_key",
    "passphrase",
    "password",
    "private_key",
    "private_pem",
    "access_token",
    "refresh_token",
    "bearer",
    "cookie",
    "mnemonic",
    "seed_phrase",
)

FORBIDDEN_BYPASS_KEYS_R6: Tuple[str, ...] = (
    "allow_live",
    "force",
    "cli_override",
    "env_approval",
    "prompt_approval",
    "qa_mock_approve",
    "skip_signature",
    "default_approve",
    "owner_waive_disk_preflight",
)

GRANT_TOP_LEVEL_KEYS_R6 = frozenset({
    "grant_schema_version",
    "grant_payload",
    "signature_algorithm",
    "key_fingerprint",
    "signature_b64",
})

GRANT_PAYLOAD_REQUIRED_KEYS_R6 = frozenset({
    "grant_id",
    "issuer",
    "trust_namespace",
    "issued_utc_ms",
    "expires_utc_ms",
    "repo",
    "branch",
    "tested_executable_sha",
    "release_manifest_sha256",
    "purpose",
    "stage",
    "venue",
    "api_family",
    "symbol",
    "product_type",
    "allowed_endpoints",
    "allowed_ws_channels",
    "window_start_ms",
    "window_end_ms",
    "destination_store_id",
    "destination_root",
    "max_session_duration_sec",
    "max_concurrency",
    "caps",
    "retention_policy",
})

R6_DURABLE_THREAT_MODEL_DISCLOSURE: Dict[str, Any] = {
    "mitigated_threats": [
        "accidental_or_adversarial_grant_replay_cross_process_and_cross_restart",
        "plan_or_payload_mutation_under_same_grant_identity",
        "crash_mid_dispatch_unknown_bytes_auto_refund",
        "sqlite_db_deletion_swap_truncation_or_rollback_vs_external_anchor",
        "clock_rollback_replay_against_ledger_high_water_mark",
        "unsigned_template_cli_env_prompt_or_qa_grant_promotion_to_external",
        "caller_supplied_alternate_ledger_path_quota_reset",
    ],
    "explicit_non_claims": [
        "does_not_defend_against_fully_compromised_os_administrator_or_root",
        "does_not_defend_against_stolen_owner_ed25519_private_signing_key",
        "qa_isolated_trust_root_tests_do_not_prove_owner_trust_root_is_provisioned",
    ],
}


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_canonical(obj: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(obj))


def _is_strict_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _strict_loads_r6(raw: bytes | str, *, max_bytes: int = MAX_GRANT_JSON_BYTES_R6) -> Any:
    if isinstance(raw, bytes):
        if len(raw) > max_bytes:
            raise ValueError(f"OVERSIZED_JSON_PAYLOAD:{len(raw)}>{max_bytes}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("INVALID_UTF8_ENCODING") from exc
    elif isinstance(raw, str):
        enc = raw.encode("utf-8", errors="strict")
        if len(enc) > max_bytes:
            raise ValueError(f"OVERSIZED_JSON_PAYLOAD:{len(enc)}>{max_bytes}")
        text = raw
    else:
        raise ValueError("RAW_INPUT_MUST_BE_BYTES_OR_STR")

    def _reject_const(tok: str) -> Any:
        raise ValueError(f"NONFINITE_JSON_CONSTANT:{tok}")

    def _reject_dups(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f"DUPLICATE_JSON_KEY:{k}")
            out[k] = v
        return out

    return json.loads(text, parse_constant=_reject_const, object_pairs_hook=_reject_dups)


def _scan_for_secrets_or_bypass(obj: Any, path: str = "") -> List[str]:
    hits: List[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            ks = str(k).lower()
            cur = f"{path}.{k}" if path else str(k)
            if any(tok in ks for tok in FORBIDDEN_SECRET_KEY_TOKENS_R6):
                hits.append(f"SECRET_KEY_FIELD:{cur}")
            if ks in FORBIDDEN_BYPASS_KEYS_R6:
                hits.append(f"FORBIDDEN_BYPASS_FIELD:{cur}")
            hits.extend(_scan_for_secrets_or_bypass(v, cur))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            hits.extend(_scan_for_secrets_or_bypass(item, f"{path}[{i}]"))
    elif isinstance(obj, str):
        if "BEGIN PRIVATE KEY" in obj or "BEGIN RSA PRIVATE KEY" in obj or "BEGIN EC PRIVATE KEY" in obj or "BEGIN OPENSSH PRIVATE KEY" in obj:
            hits.append(f"PRIVATE_KEY_MATERIAL_EMBEDDED:{path}")
    return hits


def _is_reparse_or_symlink(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
    except OSError:
        return True
    if os.name == "nt":
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
            if attrs != -1 and (attrs & 0x0400):  # FILE_ATTRIBUTE_REPARSE_POINT (symlink or NTFS junction)
                return True
        except Exception:
            pass
    return False


def _has_any_reparse_ancestor(p: Path) -> bool:
    cur = p
    for _ in range(64):
        if cur.exists() and _is_reparse_or_symlink(cur):
            return True
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def _is_network_or_unc_path(p: Path) -> bool:
    s = str(p)
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    if os.name == "nt":
        drive = os.path.splitdrive(str(p.resolve(strict=False)))[0]
        if drive and len(drive) == 2 and drive[1] == ":":
            try:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
                # 3 = DRIVE_FIXED; 4 = DRIVE_REMOTE (network)
                if drive_type == 4:
                    return True
            except Exception:
                pass
    return False


def _is_inside_directory(target: Path, container: Path) -> bool:
    try:
        t_res = target.resolve(strict=False)
        c_res = container.resolve(strict=False)
        t_res.relative_to(c_res)
        return True
    except Exception:
        return False


def _fsync_file_and_parent(path: Path) -> None:
    try:
        with path.open("rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass
    if os.name != "nt":
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def _atomic_write_json_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    _fsync_file_and_parent(path)


def _check_local_path_safety(
    path: Path,
    *,
    repo_root: Optional[Path] = None,
    must_exist: bool = True,
) -> List[str]:
    viols: List[str] = []
    if not path.is_absolute():
        viols.append(f"PATH_MUST_BE_ABSOLUTE:{path}")
    if _is_network_or_unc_path(path):
        viols.append(f"NETWORK_OR_UNC_VOLUME_FORBIDDEN:{path}")
    if _has_any_reparse_ancestor(path):
        viols.append(f"SYMLINK_OR_JUNCTION_FORBIDDEN:{path}")
    if repo_root is not None and _is_inside_directory(path, repo_root):
        viols.append(f"STORE_INSIDE_GIT_REPO_FORBIDDEN:{path}")
    if must_exist and not path.exists():
        viols.append(f"PATH_MISSING:{path}")
    if os.name != "nt" and path.exists():
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o002:
                viols.append(f"WORLD_WRITABLE_ACL_FORBIDDEN:{oct(mode)}")
        except OSError:
            viols.append("STAT_ACL_CHECK_FAILED")
    return viols


def _public_key_fingerprint(pub_key: Ed25519PublicKey) -> str:
    raw = pub_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


# =====================================================================
# F05: load_r6_owner_trust_root + isolated QA trust fixture helper
# =====================================================================
@dataclass(frozen=True)
class R6OwnerTrustRoot:
    trust_root_version: str
    trust_namespace: str
    issuer_id: str
    key_fingerprint: str
    public_key: Ed25519PublicKey
    revoked_fingerprints: Tuple[str, ...]
    revoked_grant_ids: Tuple[str, ...]
    allowed_store_ids: Tuple[str, ...]
    external_capture_permitted: bool
    source_path: str

    def redacted_summary(self) -> Dict[str, Any]:
        return {
            "trust_root_version": self.trust_root_version,
            "trust_namespace": self.trust_namespace,
            "issuer_id": self.issuer_id,
            "key_fingerprint": self.key_fingerprint,
            "revoked_fingerprints_count": len(self.revoked_fingerprints),
            "revoked_grant_ids_count": len(self.revoked_grant_ids),
            "allowed_store_ids": list(self.allowed_store_ids),
            "external_capture_permitted": self.external_capture_permitted,
        }


def load_r6_owner_trust_root(
    configured_path: Optional[str | Path],
    *,
    expected_namespace: str = OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Loads an independently provisioned local Ed25519 owner trust root outside Git.
    ZERO embedded default owner keys; ZERO trust-on-first-use.
    Returns BLOCKED_OWNER_TRUST_ROOT_NOT_PROVISIONED if no valid local trust root is configured.
    """
    if configured_path is None or not str(configured_path).strip():
        return {
            "loaded": False,
            "status": "BLOCKED_OWNER_TRUST_ROOT_NOT_PROVISIONED",
            "trust_root": None,
            "violations": ["OWNER_TRUST_ROOT_PATH_NOT_CONFIGURED"],
        }

    p = Path(configured_path)
    viols = _check_local_path_safety(p, repo_root=repo_root, must_exist=True)
    if viols:
        return {
            "loaded": False,
            "status": "BLOCKED_OWNER_TRUST_ROOT_NOT_PROVISIONED" if any("PATH_MISSING" in v for v in viols) else "BLOCKED_TRUST_ROOT_SECURITY_VIOLATION",
            "trust_root": None,
            "violations": viols,
        }
    if not p.is_file():
        return {
            "loaded": False,
            "status": "BLOCKED_TRUST_ROOT_SECURITY_VIOLATION",
            "trust_root": None,
            "violations": ["TRUST_ROOT_NOT_REGULAR_FILE"],
        }

    try:
        doc = _strict_loads_r6(p.read_bytes(), max_bytes=32_768)
    except Exception as exc:
        return {
            "loaded": False,
            "status": "BLOCKED_TRUST_ROOT_INVALID",
            "trust_root": None,
            "violations": [f"TRUST_ROOT_PARSE_ERROR:{exc}"],
        }

    if not isinstance(doc, Mapping):
        return {"loaded": False, "status": "BLOCKED_TRUST_ROOT_INVALID", "trust_root": None, "violations": ["TRUST_ROOT_NOT_OBJECT"]}

    sec_hits = _scan_for_secrets_or_bypass(doc)
    if sec_hits:
        return {"loaded": False, "status": "BLOCKED_TRUST_ROOT_SECRET_LEAK", "trust_root": None, "violations": sec_hits}

    if doc.get("schema_version") != TRUST_ROOT_SCHEMA_VERSION_R6:
        viols.append(f"TRUST_ROOT_SCHEMA_MISMATCH:{doc.get('schema_version')}")

    ns = str(doc.get("trust_namespace") or "").strip()
    if ns != expected_namespace:
        viols.append(f"TRUST_NAMESPACE_MISMATCH:expected={expected_namespace}:got={ns}")

    issuer_id = str(doc.get("issuer_id") or "").strip()
    if not issuer_id:
        viols.append("TRUST_ROOT_MISSING_ISSUER_ID")

    pub_pem = str(doc.get("ed25519_public_key_pem") or "").strip()
    pub_key: Optional[Ed25519PublicKey] = None
    computed_fp = ""
    try:
        loaded_k = serialization.load_pem_public_key(pub_pem.encode("utf-8"))
        if not isinstance(loaded_k, Ed25519PublicKey):
            viols.append("TRUST_ROOT_KEY_NOT_ED25519")
        else:
            pub_key = loaded_k
            computed_fp = _public_key_fingerprint(pub_key)
    except Exception as exc:
        viols.append(f"TRUST_ROOT_PUBLIC_KEY_INVALID:{exc}")

    declared_fp = str(doc.get("key_fingerprint") or "").strip().lower()
    if pub_key is not None and declared_fp != computed_fp:
        viols.append("TRUST_ROOT_FINGERPRINT_MISMATCH")

    revoked_fps = tuple(str(x).strip().lower() for x in (doc.get("revoked_fingerprints") or ()))
    revoked_gids = tuple(str(x).strip() for x in (doc.get("revoked_grant_ids") or ()))
    if computed_fp and computed_fp in revoked_fps:
        viols.append("TRUST_ROOT_KEY_REVOKED")
    if doc.get("revoked") is True:
        viols.append("TRUST_ROOT_REVOKED")

    allowed_stores = tuple(str(x).strip() for x in (doc.get("allowed_store_ids") or ()) if str(x).strip())
    if not allowed_stores:
        viols.append("TRUST_ROOT_MISSING_ALLOWED_STORE_IDS")

    ext_perm = doc.get("external_capture_permitted") is True
    if ns == QA_ISOLATED_TRUST_NAMESPACE_R6 and ext_perm:
        viols.append("QA_TRUST_NAMESPACE_CANNOT_PERMIT_EXTERNAL_CAPTURE")

    if viols or pub_key is None:
        return {
            "loaded": False,
            "status": "BLOCKED_TRUST_ROOT_INVALID",
            "trust_root": None,
            "violations": viols,
        }

    tr = R6OwnerTrustRoot(
        trust_root_version=str(doc.get("version") or "1"),
        trust_namespace=ns,
        issuer_id=issuer_id,
        key_fingerprint=computed_fp,
        public_key=pub_key,
        revoked_fingerprints=revoked_fps,
        revoked_grant_ids=revoked_gids,
        allowed_store_ids=allowed_stores,
        external_capture_permitted=(ns == OWNER_EXTERNAL_TRUST_NAMESPACE_R6 and ext_perm),
        source_path=str(p.resolve()),
    )
    return {
        "loaded": True,
        "status": "TRUST_ROOT_LOADED_QA_ISOLATED" if ns == QA_ISOLATED_TRUST_NAMESPACE_R6 else "TRUST_ROOT_LOADED_OWNER_EXTERNAL",
        "trust_root": tr,
        "violations": [],
    }


def provision_qa_isolated_trust_root(
    qa_dir: Path,
    *,
    store_id: str,
    issuer_id: str = "QA_ISOLATED_HARNESS",
    revoked: bool = False,
) -> Tuple[Path, Ed25519PrivateKey, str]:
    """
    Creates an ephemeral Ed25519 keypair + QA-isolated trust root JSON strictly inside a temporary
    QA directory (NEVER usable for external B_SMOKE or C_DEV capture).
    """
    qa_dir.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    fp = _public_key_fingerprint(pub)
    tr_path = qa_dir / "qa_isolated_trust_root.json"
    doc = {
        "schema_version": TRUST_ROOT_SCHEMA_VERSION_R6,
        "version": "1",
        "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
        "issuer_id": issuer_id,
        "key_fingerprint": fp,
        "ed25519_public_key_pem": pub_pem,
        "revoked": bool(revoked),
        "revoked_fingerprints": [fp] if revoked else [],
        "revoked_grant_ids": [],
        "allowed_store_ids": [store_id],
        "external_capture_permitted": False,
    }
    _atomic_write_json_fsync(tr_path, doc)
    return tr_path, priv, fp


def sign_r6_grant_payload_with_ed25519(
    grant_payload: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
) -> str:
    """Signs canonical UTF-8 JSON of `grant_payload` using Ed25519 and returns complete grant JSON string."""
    pub = private_key.public_key()
    fp = _public_key_fingerprint(pub)
    canon = _canonical_json_bytes(dict(grant_payload))
    sig = private_key.sign(canon)
    envelope = {
        "grant_schema_version": GRANT_SCHEMA_VERSION_R6,
        "grant_payload": dict(grant_payload),
        "signature_algorithm": "ED25519",
        "key_fingerprint": fp,
        "signature_b64": base64.b64encode(sig).decode("ascii"),
    }
    return json.dumps(envelope, sort_keys=True, indent=2, ensure_ascii=False)


# =====================================================================
# F07: canonical_r6_grant_identity
# =====================================================================
def canonical_r6_grant_identity(
    grant_payload: Mapping[str, Any],
    trust_namespace: str,
) -> Dict[str, str]:
    """
    Computes stable logical grant identity from trust_namespace + issuer + immutable grant_id
    (NEVER incorporating run_id, session_id, or verification nonce).
    Also computes full canonical payload digest so any payload drift under the same identity is caught.
    """
    issuer = str(grant_payload.get("issuer") or "").strip()
    gid = str(grant_payload.get("grant_id") or "").strip()
    ns = str(trust_namespace or "").strip()
    if not issuer or not gid or not ns:
        raise ValueError("CANNOT_DERIVE_GRANT_IDENTITY_MISSING_FIELDS")
    id_key = hashlib.sha256(f"{ns}|{issuer}|{gid}".encode("utf-8")).hexdigest()
    payload_digest = _sha256_canonical(dict(grant_payload))
    return {
        "grant_identity_key": id_key,
        "grant_payload_digest": payload_digest,
        "trust_namespace": ns,
        "issuer": issuer,
        "grant_id": gid,
    }


# =====================================================================
# F06: verify_r6_owner_grant
# =====================================================================
@dataclass(frozen=True)
class R6VerifiedGrant:
    grant_identity_key: str
    grant_payload_digest: str
    grant_id: str
    issuer: str
    trust_namespace: str
    key_fingerprint: str
    issued_utc_ms: int
    expires_utc_ms: int
    repo: str
    branch: str
    tested_executable_sha: str
    release_manifest_sha256: str
    purpose: str  # "DEV_ONLY" | "QA_FIXTURE"
    stage: str  # "B_SMOKE" | "C_DEV" | "QA_LOOPBACK"
    venue: str
    api_family: str
    symbol: str
    product_type: str
    allowed_endpoints: Tuple[str, ...]
    allowed_ws_channels: Tuple[str, ...]
    window_start_ms: int
    window_end_ms: int
    destination_store_id: str
    destination_root: str
    max_session_duration_sec: int
    max_concurrency: int
    caps: Mapping[str, int]
    retention_policy: Mapping[str, Any]
    external_capture_permitted: bool

    def to_redacted_dict(self) -> Dict[str, Any]:
        return {
            "grant_identity_key": self.grant_identity_key,
            "grant_payload_digest": self.grant_payload_digest,
            "grant_id": self.grant_id,
            "issuer": self.issuer,
            "trust_namespace": self.trust_namespace,
            "key_fingerprint": self.key_fingerprint,
            "issued_utc_ms": self.issued_utc_ms,
            "expires_utc_ms": self.expires_utc_ms,
            "repo": self.repo,
            "branch": self.branch,
            "tested_executable_sha": self.tested_executable_sha,
            "release_manifest_sha256": self.release_manifest_sha256,
            "purpose": self.purpose,
            "stage": self.stage,
            "venue": self.venue,
            "api_family": self.api_family,
            "symbol": self.symbol,
            "product_type": self.product_type,
            "allowed_endpoints": list(self.allowed_endpoints),
            "allowed_ws_channels": list(self.allowed_ws_channels),
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "destination_store_id": self.destination_store_id,
            "max_session_duration_sec": self.max_session_duration_sec,
            "max_concurrency": self.max_concurrency,
            "caps": dict(self.caps),
            "external_capture_permitted": self.external_capture_permitted,
        }


def verify_r6_owner_grant(
    raw_grant: bytes | str,
    trust_root: Optional[R6OwnerTrustRoot],
    actual_now: datetime,
    *,
    expected_repo: str = "quocan050302/Build-app-crypto",
    expected_branch: str = MANDATORY_BRANCH_KH03_R6,
    expected_executable_sha: str,
    expected_release_manifest_sha256: str,
    expected_store_id: str,
    expected_stage: str,
    expected_destination_root: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    ledger_high_water_utc_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Strictly verifies a raw serialized Ed25519-signed R6 owner grant against an independently
    loaded trust root, exact frozen executable SHA, release manifest SHA, stage, store identity,
    timestamps, clock rollback guard, and all finite budget caps.
    """
    violations: List[str] = []

    if trust_root is None or not isinstance(trust_root, R6OwnerTrustRoot):
        return {
            "valid": False,
            "status": "BLOCKED_OWNER_AUTHORIZATION",
            "verified_grant": None,
            "violations": ["TRUST_ROOT_NOT_PROVISIONED"],
        }

    try:
        envelope = _strict_loads_r6(raw_grant, max_bytes=MAX_GRANT_JSON_BYTES_R6)
    except Exception as exc:
        return {
            "valid": False,
            "status": "REJECTED_MALFORMED_GRANT",
            "verified_grant": None,
            "violations": [f"GRANT_PARSE_ERROR:{exc}"],
        }

    if not isinstance(envelope, Mapping):
        return {"valid": False, "status": "REJECTED_MALFORMED_GRANT", "verified_grant": None, "violations": ["GRANT_NOT_OBJECT"]}

    if envelope.get("template_status") == "UNSIGNED_OWNER_REVIEW_TEMPLATE" or envelope.get("unsigned_template") is True:
        return {
            "valid": False,
            "status": "BLOCKED_UNSIGNED_REVIEW_TEMPLATE",
            "verified_grant": None,
            "violations": ["UNSIGNED_OWNER_REVIEW_TEMPLATE_NOT_VALID_AUTHORIZATION"],
        }

    sec_hits = _scan_for_secrets_or_bypass(envelope)
    if sec_hits:
        return {"valid": False, "status": "REJECTED_SECRET_OR_BYPASS", "verified_grant": None, "violations": sec_hits}

    extra_top = sorted(set(envelope.keys()) - GRANT_TOP_LEVEL_KEYS_R6)
    missing_top = sorted(GRANT_TOP_LEVEL_KEYS_R6 - set(envelope.keys()))
    if extra_top:
        violations.append(f"UNKNOWN_TOP_LEVEL_KEYS:{','.join(extra_top)}")
    if missing_top:
        violations.append(f"MISSING_TOP_LEVEL_KEYS:{','.join(missing_top)}")

    if envelope.get("grant_schema_version") != GRANT_SCHEMA_VERSION_R6:
        violations.append(f"GRANT_SCHEMA_VERSION_MISMATCH:{envelope.get('grant_schema_version')}")
    if envelope.get("signature_algorithm") != "ED25519":
        violations.append(f"UNSUPPORTED_SIGNATURE_ALGORITHM:{envelope.get('signature_algorithm')}")

    gp = envelope.get("grant_payload")
    if not isinstance(gp, Mapping):
        violations.append("GRANT_PAYLOAD_NOT_OBJECT")
        return {"valid": False, "status": "REJECTED_MALFORMED_GRANT", "verified_grant": None, "violations": violations}

    extra_gp = sorted(set(gp.keys()) - GRANT_PAYLOAD_REQUIRED_KEYS_R6)
    missing_gp = sorted(GRANT_PAYLOAD_REQUIRED_KEYS_R6 - set(gp.keys()))
    if extra_gp:
        violations.append(f"UNKNOWN_PAYLOAD_KEYS:{','.join(extra_gp)}")
    if missing_gp:
        violations.append(f"MISSING_PAYLOAD_KEYS:{','.join(missing_gp)}")

    # Cryptographic Ed25519 signature verification over canonical JSON of grant_payload
    fp = str(envelope.get("key_fingerprint") or "").strip().lower()
    if fp != trust_root.key_fingerprint:
        violations.append("KEY_FINGERPRINT_MISMATCH")
    if fp in trust_root.revoked_fingerprints:
        violations.append("SIGNING_KEY_REVOKED")

    sig_b64 = str(envelope.get("signature_b64") or "").strip()
    if not sig_b64:
        violations.append("MISSING_SIGNATURE")
    else:
        try:
            sig_bytes = base64.b64decode(sig_b64, validate=True)
            trust_root.public_key.verify(sig_bytes, _canonical_json_bytes(dict(gp)))
        except InvalidSignature:
            violations.append("INVALID_ED25519_SIGNATURE")
        except Exception as exc:
            violations.append(f"SIGNATURE_DECODE_OR_VERIFY_ERROR:{exc}")

    # Semantic field validation
    gid = str(gp.get("grant_id") or "").strip()
    issuer = str(gp.get("issuer") or "").strip()
    ns = str(gp.get("trust_namespace") or "").strip()
    if not gid or len(gid) > 128:
        violations.append("INVALID_GRANT_ID")
    if gid in trust_root.revoked_grant_ids:
        violations.append(f"GRANT_REVOKED:{gid}")
    if issuer != trust_root.issuer_id:
        violations.append(f"ISSUER_MISMATCH:expected={trust_root.issuer_id}:got={issuer}")
    if ns != trust_root.trust_namespace:
        violations.append(f"GRANT_TRUST_NAMESPACE_MISMATCH:expected={trust_root.trust_namespace}:got={ns}")

    stage = str(gp.get("stage") or "").strip()
    purpose = str(gp.get("purpose") or "").strip()
    if stage != expected_stage:
        violations.append(f"STAGE_MISMATCH:expected={expected_stage}:got={stage}")

    is_external_stage = expected_stage in ALLOWED_STAGES_EXTERNAL_R6
    if is_external_stage:
        if trust_root.trust_namespace != OWNER_EXTERNAL_TRUST_NAMESPACE_R6 or not trust_root.external_capture_permitted:
            violations.append("QA_OR_UNTRUSTED_NAMESPACE_FORBIDDEN_FOR_EXTERNAL_STAGE")
        if purpose != "DEV_ONLY":
            violations.append(f"EXTERNAL_PURPOSE_MUST_BE_DEV_ONLY:{purpose}")
    elif expected_stage in ALLOWED_STAGES_QA_R6:
        if trust_root.trust_namespace != QA_ISOLATED_TRUST_NAMESPACE_R6:
            violations.append("QA_STAGE_REQUIRES_QA_ISOLATED_TRUST_NAMESPACE")
        if purpose != "QA_FIXTURE":
            violations.append(f"QA_PURPOSE_MUST_BE_QA_FIXTURE:{purpose}")
    else:
        violations.append(f"UNSUPPORTED_STAGE:{expected_stage}")

    if str(gp.get("repo") or "").strip() != expected_repo:
        violations.append(f"REPO_MISMATCH:{gp.get('repo')}")
    if str(gp.get("branch") or "").strip() != expected_branch:
        violations.append(f"BRANCH_MISMATCH:{gp.get('branch')}")
    if str(gp.get("tested_executable_sha") or "").strip() != expected_executable_sha:
        violations.append("EXECUTABLE_SHA_MISMATCH")
    if str(gp.get("release_manifest_sha256") or "").strip() != expected_release_manifest_sha256:
        violations.append("RELEASE_MANIFEST_SHA_MISMATCH")

    if str(gp.get("venue") or "").strip() != APPROVED_VENUE_R6:
        violations.append(f"VENUE_MISMATCH:{gp.get('venue')}")
    if str(gp.get("api_family") or "").strip() != API_FAMILY_CLASSIC_V2:
        violations.append(f"API_FAMILY_MISMATCH:{gp.get('api_family')}")
    if str(gp.get("symbol") or "").strip() != APPROVED_SYMBOL_R6:
        violations.append(f"SYMBOL_MISMATCH:{gp.get('symbol')}")
    if str(gp.get("product_type") or "").strip() != APPROVED_PRODUCT_TYPE_V2_R6:
        violations.append(f"PRODUCT_TYPE_MISMATCH:{gp.get('product_type')}")

    store_id = str(gp.get("destination_store_id") or "").strip()
    if store_id != expected_store_id or store_id not in trust_root.allowed_store_ids:
        violations.append(f"STORE_IDENTITY_MISMATCH:{store_id}")

    dest_root_str = str(gp.get("destination_root") or "").strip()
    if not dest_root_str:
        violations.append("MISSING_DESTINATION_ROOT")
    else:
        dest_p = Path(dest_root_str)
        violations.extend(_check_local_path_safety(dest_p, repo_root=repo_root, must_exist=False))
        if expected_destination_root is not None:
            if dest_p.resolve(strict=False) != expected_destination_root.resolve(strict=False):
                violations.append("DESTINATION_ROOT_MISMATCH")

    # Strict integer timestamps & clock rollback check
    now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
    issued_ms = gp.get("issued_utc_ms")
    expires_ms = gp.get("expires_utc_ms")
    win_start = gp.get("window_start_ms")
    win_end = gp.get("window_end_ms")
    max_dur = gp.get("max_session_duration_sec")
    max_conc = gp.get("max_concurrency")

    for k, v in (
        ("issued_utc_ms", issued_ms),
        ("expires_utc_ms", expires_ms),
        ("window_start_ms", win_start),
        ("window_end_ms", win_end),
        ("max_session_duration_sec", max_dur),
        ("max_concurrency", max_conc),
    ):
        if not _is_strict_int(v) or int(v) <= 0 or int(v) > MAX_INT64_SAFE_R6:
            violations.append(f"INVALID_STRICT_INT:{k}")

    if _is_strict_int(issued_ms) and _is_strict_int(expires_ms):
        if issued_ms >= expires_ms:
            violations.append("GRANT_ISSUED_AFTER_EXPIRY")
        if now_ms >= expires_ms:
            violations.append("GRANT_EXPIRED")
        if issued_ms > now_ms + MAX_CLOCK_SKEW_MS_R6:
            violations.append("GRANT_ISSUED_IN_FUTURE_OR_CLOCK_ROLLBACK")
        if ledger_high_water_utc_ms is not None and now_ms + MAX_CLOCK_SKEW_MS_R6 < ledger_high_water_utc_ms:
            violations.append("CLOCK_ROLLBACK_BELOW_LEDGER_HIGH_WATER_MARK")

    if _is_strict_int(win_start) and _is_strict_int(win_end) and win_start >= win_end:
        violations.append("INVALID_DATA_WINDOW_ORDER")

    conc_limit = 2 if is_external_stage else 4
    if _is_strict_int(max_conc) and (max_conc < 1 or max_conc > conc_limit):
        violations.append(f"CONCURRENCY_EXCEEDS_CAP:{max_conc}>{conc_limit}")

    if _is_strict_int(max_dur):
        stage_max_dur = 300 if stage == "B_SMOKE" else (3600 if stage == "C_DEV" else 600)
        if max_dur > stage_max_dur:
            violations.append(f"SESSION_DURATION_EXCEEDS_STAGE_CAP:{max_dur}>{stage_max_dur}")

    # Strict finite positive integer caps
    raw_caps = gp.get("caps")
    validated_caps: Dict[str, int] = {}
    if not isinstance(raw_caps, Mapping):
        violations.append("CAPS_NOT_MAPPING")
    else:
        extra_caps = sorted(set(raw_caps.keys()) - set(BUDGET_FIELDS_R6))
        missing_caps = sorted(set(BUDGET_FIELDS_R6) - set(raw_caps.keys()))
        if extra_caps:
            violations.append(f"UNKNOWN_CAP_KEYS:{','.join(extra_caps)}")
        if missing_caps:
            violations.append(f"MISSING_CAP_KEYS:{','.join(missing_caps)}")
        for ck in BUDGET_FIELDS_R6:
            cv = raw_caps.get(ck)
            if not _is_strict_int(cv) or int(cv) < 0 or int(cv) > MAX_INT64_SAFE_R6:
                violations.append(f"INVALID_CAP_INT:{ck}")
            else:
                validated_caps[ck] = int(cv)
        # Stage ceiling enforcement
        if stage == "B_SMOKE":
            smoke_ceilings = {
                "requests": 30,
                "connections": 2,
                "subscriptions": 2,
                "payload_bytes": 10 * 1024 * 1024,
                "messages": 10_000,
            }
            for k, ceil_v in smoke_ceilings.items():
                if k in validated_caps and validated_caps[k] > ceil_v:
                    violations.append(f"SMOKE_CAP_EXCEEDS_POLICY:{k}:{validated_caps[k]}>{ceil_v}")
        elif stage == "C_DEV":
            dev_ceilings = {
                "requests": 500,
                "connections": 4,
                "subscriptions": 4,
                "payload_bytes": 50 * 1024 * 1024,
                "messages": 200_000,
            }
            for k, ceil_v in dev_ceilings.items():
                if k in validated_caps and validated_caps[k] > ceil_v:
                    violations.append(f"DEV_CAP_EXCEEDS_POLICY:{k}:{validated_caps[k]}>{ceil_v}")

    allowed_eps = gp.get("allowed_endpoints")
    allowed_ws = gp.get("allowed_ws_channels")
    if not isinstance(allowed_eps, list) or not allowed_eps or not all(isinstance(x, str) and x.strip() for x in allowed_eps):
        violations.append("INVALID_ALLOWED_ENDPOINTS")
    if not isinstance(allowed_ws, list) or not all(isinstance(x, str) and x.strip() for x in allowed_ws):
        violations.append("INVALID_ALLOWED_WS_CHANNELS")

    ret_pol = gp.get("retention_policy")
    if not isinstance(ret_pol, Mapping) or not _is_strict_int(ret_pol.get("max_retention_days")) or int(ret_pol["max_retention_days"]) <= 0:
        violations.append("INVALID_RETENTION_POLICY")

    if violations:
        return {
            "valid": False,
            "status": "REJECTED_GRANT_VERIFICATION",
            "verified_grant": None,
            "violations": violations,
        }

    ident = canonical_r6_grant_identity(gp, trust_root.trust_namespace)
    vg = R6VerifiedGrant(
        grant_identity_key=ident["grant_identity_key"],
        grant_payload_digest=ident["grant_payload_digest"],
        grant_id=gid,
        issuer=issuer,
        trust_namespace=trust_root.trust_namespace,
        key_fingerprint=fp,
        issued_utc_ms=int(issued_ms),
        expires_utc_ms=int(expires_ms),
        repo=str(gp["repo"]),
        branch=str(gp["branch"]),
        tested_executable_sha=str(gp["tested_executable_sha"]),
        release_manifest_sha256=str(gp["release_manifest_sha256"]),
        purpose=purpose,
        stage=stage,
        venue=str(gp["venue"]),
        api_family=str(gp["api_family"]),
        symbol=str(gp["symbol"]),
        product_type=str(gp["product_type"]),
        allowed_endpoints=tuple(str(x) for x in allowed_eps),
        allowed_ws_channels=tuple(str(x) for x in allowed_ws),
        window_start_ms=int(win_start),
        window_end_ms=int(win_end),
        destination_store_id=store_id,
        destination_root=str(Path(dest_root_str).resolve(strict=False)),
        max_session_duration_sec=int(max_dur),
        max_concurrency=int(max_conc),
        caps=validated_caps,
        retention_policy=dict(ret_pol),
        external_capture_permitted=(is_external_stage and trust_root.external_capture_permitted),
    )
    return {
        "valid": True,
        "status": "GRANT_VERIFIED_EXTERNAL" if vg.external_capture_permitted else "GRANT_VERIFIED_QA_ISOLATED",
        "verified_grant": vg,
        "violations": [],
    }


# =====================================================================
# F08..F12: Durable SQLite + External Anchor One-Shot Ledger
# =====================================================================
@dataclass(frozen=True)
class R6DurableSessionLease:
    lease_id: str
    grant_identity_key: str
    grant_payload_digest: str
    plan_digest: str
    session_id: str
    store_id: str
    trust_namespace: str
    stage: str
    expires_utc_ms: int
    claimed_generation: int
    external_capture_permitted: bool


_LEDGER_DDL_R6 = """
CREATE TABLE ledger_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version TEXT NOT NULL,
    store_id TEXT NOT NULL,
    trust_namespace TEXT NOT NULL,
    generation INTEGER NOT NULL,
    audit_tip_hash TEXT NOT NULL,
    high_water_utc_ms INTEGER NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT NOT NULL DEFAULT '',
    max_grants INTEGER NOT NULL,
    max_db_bytes INTEGER NOT NULL
);

CREATE TABLE grants (
    grant_identity_key TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    issuer TEXT NOT NULL,
    trust_namespace TEXT NOT NULL,
    grant_payload_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    state TEXT NOT NULL,
    expires_utc_ms INTEGER NOT NULL,
    claimed_utc_ms INTEGER NOT NULL,
    finalized_utc_ms INTEGER,
    caps_json TEXT NOT NULL,
    known_json TEXT NOT NULL,
    uncertain_json TEXT NOT NULL,
    outstanding_json TEXT NOT NULL,
    final_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    grant_identity_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    state TEXT NOT NULL,
    reserved_json TEXT NOT NULL,
    known_json TEXT NOT NULL,
    uncertain_json TEXT NOT NULL,
    created_utc_ms INTEGER NOT NULL,
    started_utc_ms INTEGER,
    settled_utc_ms INTEGER,
    outcome_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (grant_identity_key) REFERENCES grants(grant_identity_key)
);

CREATE TABLE audit_chain (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    generation INTEGER NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    event_utc_ms INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
"""


def _zero_budget_dict() -> Dict[str, int]:
    return {k: 0 for k in BUDGET_FIELDS_R6}


def _compute_audit_entry_hash(generation: int, event_type: str, event_utc_ms: int, payload_json: str, prev_hash: str) -> str:
    return _sha256_canonical({
        "generation": generation,
        "event_type": event_type,
        "event_utc_ms": event_utc_ms,
        "payload_json": payload_json,
        "prev_hash": prev_hash,
    })


def _compute_anchor_doc(
    *,
    store_id: str,
    trust_namespace: str,
    generation: int,
    audit_tip_hash: str,
    high_water_utc_ms: int,
    quarantined: bool,
    quarantine_reason: str,
) -> Dict[str, Any]:
    body = {
        "schema_version": LEDGER_SCHEMA_VERSION_R6,
        "store_id": store_id,
        "trust_namespace": trust_namespace,
        "generation": int(generation),
        "audit_tip_hash": str(audit_tip_hash),
        "high_water_utc_ms": int(high_water_utc_ms),
        "quarantined": bool(quarantined),
        "quarantine_reason": str(quarantine_reason),
    }
    body["anchor_sha256"] = _sha256_canonical(body)
    return body


def provision_r6_durable_ledger(
    store_root: Path,
    *,
    store_id: str,
    trust_namespace: str,
    repo_root: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
    max_grants: int = DEFAULT_LEDGER_MAX_GRANTS_R6,
    max_db_bytes: int = DEFAULT_LEDGER_MAX_BYTES_R6,
) -> Dict[str, Any]:
    """
    Explicitly provisions a brand-new R6 durable SQLite grant ledger + external anchor.
    NEVER called automatically by `open_r6_durable_ledger`! Refuses to overwrite existing store.
    """
    viols = _check_local_path_safety(store_root, repo_root=repo_root, must_exist=False)
    if viols:
        return {"provisioned": False, "status": "BLOCKED_PROVISION_SECURITY", "violations": viols}
    if not str(store_id or "").strip():
        return {"provisioned": False, "status": "BLOCKED_PROVISION_INVALID", "violations": ["EMPTY_STORE_ID"]}

    store_root.mkdir(parents=True, exist_ok=True)
    db_path = store_root / "ledger.sqlite3"
    anchor_path = store_root / "ledger.anchor.json"
    if db_path.exists() or anchor_path.exists():
        return {
            "provisioned": False,
            "status": "REJECTED_STORE_ALREADY_EXISTS",
            "violations": ["EXISTING_LEDGER_REFUSED_NO_DESTRUCTIVE_RESET"],
        }

    now_ms = int((now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc).timestamp() * 1000)
    genesis_prev = "0" * 64
    genesis_payload = json.dumps({"store_id": store_id, "trust_namespace": trust_namespace}, sort_keys=True, separators=(",", ":"))
    genesis_hash = _compute_audit_entry_hash(1, "LEDGER_PROVISIONED", now_ms, genesis_payload, genesis_prev)

    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_LEDGER_DDL_R6)
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """
            INSERT INTO ledger_meta (
                id, schema_version, store_id, trust_namespace, generation,
                audit_tip_hash, high_water_utc_ms, quarantined, quarantine_reason,
                max_grants, max_db_bytes
            ) VALUES (1, ?, ?, ?, 1, ?, ?, 0, '', ?, ?)
            """,
            (LEDGER_SCHEMA_VERSION_R6, store_id, trust_namespace, genesis_hash, now_ms, int(max_grants), int(max_db_bytes)),
        )
        conn.execute(
            "INSERT INTO audit_chain (generation, event_type, event_utc_ms, payload_json, prev_hash, entry_hash) VALUES (1, ?, ?, ?, ?, ?)",
            ("LEDGER_PROVISIONED", now_ms, genesis_payload, genesis_prev, genesis_hash),
        )
        conn.execute("COMMIT;")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()

    anchor = _compute_anchor_doc(
        store_id=store_id,
        trust_namespace=trust_namespace,
        generation=1,
        audit_tip_hash=genesis_hash,
        high_water_utc_ms=now_ms,
        quarantined=False,
        quarantine_reason="",
    )
    _atomic_write_json_fsync(anchor_path, anchor)
    return {
        "provisioned": True,
        "status": "PROVISIONED",
        "store_id": store_id,
        "trust_namespace": trust_namespace,
        "db_path": str(db_path),
        "anchor_path": str(anchor_path),
        "generation": 1,
        "audit_tip_hash": genesis_hash,
    }


class R6DurableGrantLedger:
    """
    Cross-process & cross-restart durable one-shot owner authorization ledger backed by SQLite
    (`BEGIN IMMEDIATE`, `WAL`, `synchronous=FULL`) + external generation anchor.
    """

    def __init__(
        self,
        *,
        store_root: Path,
        db_path: Path,
        anchor_path: Path,
        store_id: str,
        trust_namespace: str,
        max_grants: int,
        max_db_bytes: int,
        busy_timeout_ms: int = 10_000,
    ) -> None:
        self.store_root = store_root
        self.db_path = db_path
        self.anchor_path = anchor_path
        self.store_id = store_id
        self.trust_namespace = trust_namespace
        self.max_grants = int(max_grants)
        self.max_db_bytes = int(max_db_bytes)
        self.busy_timeout_ms = int(busy_timeout_ms)

    def _connect(self) -> sqlite3.Connection:
        # URI mode=rw ensures SQLite NEVER silently creates a missing DB file!
        uri = f"{self.db_path.resolve().as_uri()}?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _sync_anchor_from_meta(self, meta_row: Mapping[str, Any]) -> None:
        anchor = _compute_anchor_doc(
            store_id=str(meta_row["store_id"]),
            trust_namespace=str(meta_row["trust_namespace"]),
            generation=int(meta_row["generation"]),
            audit_tip_hash=str(meta_row["audit_tip_hash"]),
            high_water_utc_ms=int(meta_row["high_water_utc_ms"]),
            quarantined=bool(meta_row["quarantined"]),
            quarantine_reason=str(meta_row["quarantine_reason"]),
        )
        _atomic_write_json_fsync(self.anchor_path, anchor)

    def _append_audit_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        event_type: str,
        event_utc_ms: int,
        payload: Mapping[str, Any],
        set_quarantine: Optional[str] = None,
    ) -> Dict[str, Any]:
        meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
        new_gen = int(meta["generation"]) + 1
        prev_hash = str(meta["audit_tip_hash"])
        new_hw = max(int(meta["high_water_utc_ms"]), int(event_utc_ms))
        p_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        entry_hash = _compute_audit_entry_hash(new_gen, event_type, int(event_utc_ms), p_json, prev_hash)
        q_flag = 1 if set_quarantine else int(meta["quarantined"])
        q_reason = set_quarantine if set_quarantine else str(meta["quarantine_reason"])

        conn.execute(
            "INSERT INTO audit_chain (generation, event_type, event_utc_ms, payload_json, prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (new_gen, event_type, int(event_utc_ms), p_json, prev_hash, entry_hash),
        )
        conn.execute(
            "UPDATE ledger_meta SET generation = ?, audit_tip_hash = ?, high_water_utc_ms = ?, quarantined = ?, quarantine_reason = ? WHERE id = 1",
            (new_gen, entry_hash, new_hw, q_flag, q_reason),
        )
        return {
            "store_id": str(meta["store_id"]),
            "trust_namespace": str(meta["trust_namespace"]),
            "generation": new_gen,
            "audit_tip_hash": entry_hash,
            "high_water_utc_ms": new_hw,
            "quarantined": bool(q_flag),
            "quarantine_reason": q_reason,
        }

    def _check_capacity_in_tx(self, conn: sqlite3.Connection) -> Optional[str]:
        g_count = int(conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0])
        if g_count >= self.max_grants:
            return f"LEDGER_CAPACITY_GRANTS_FULL:{g_count}>={self.max_grants}"
        try:
            db_sz = self.db_path.stat().st_size
            wal_p = Path(str(self.db_path) + "-wal")
            if wal_p.exists():
                db_sz += wal_p.stat().st_size
            if db_sz >= self.max_db_bytes:
                return f"LEDGER_CAPACITY_BYTES_FULL:{db_sz}>={self.max_db_bytes}"
        except OSError:
            return "LEDGER_DB_STAT_UNHEALTHY"
        return None

    def get_high_water_utc_ms(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT high_water_utc_ms FROM ledger_meta WHERE id = 1").fetchone()
            return int(row["high_water_utc_ms"])
        finally:
            conn.close()

    # F09: Atomic one-shot grant claim
    def claim_r6_grant_session_atomic(
        self,
        verified_grant: R6VerifiedGrant,
        verified_plan_digest: str,
        session_id: str,
        *,
        actual_now: datetime,
        crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(verified_grant, R6VerifiedGrant):
            return {"claimed": False, "status": "REJECTED_INVALID_GRANT_OBJECT", "lease": None, "violations": ["INVALID_GRANT_TYPE"]}
        if not str(verified_plan_digest or "").strip() or not str(session_id or "").strip():
            return {"claimed": False, "status": "REJECTED_MISSING_PLAN_OR_SESSION", "lease": None, "violations": ["MISSING_PLAN_OR_SESSION_ID"]}
        if verified_grant.destination_store_id != self.store_id or verified_grant.trust_namespace != self.trust_namespace:
            return {
                "claimed": False,
                "status": "REJECTED_STORE_OR_NAMESPACE_MISMATCH",
                "lease": None,
                "violations": ["GRANT_STORE_OR_TRUST_NAMESPACE_MISMATCH"],
            }

        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        if now_ms >= verified_grant.expires_utc_ms:
            return {"claimed": False, "status": "REJECTED_GRANT_EXPIRED", "lease": None, "violations": ["GRANT_EXPIRED_AT_CLAIM"]}

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
            if int(meta["quarantined"]) != 0:
                conn.execute("ROLLBACK;")
                return {
                    "claimed": False,
                    "status": "BLOCKED_LEDGER_QUARANTINED",
                    "lease": None,
                    "violations": [f"LEDGER_QUARANTINED:{meta['quarantine_reason']}"],
                }
            if now_ms + MAX_CLOCK_SKEW_MS_R6 < int(meta["high_water_utc_ms"]):
                conn.execute("ROLLBACK;")
                return {
                    "claimed": False,
                    "status": "BLOCKED_CLOCK_ROLLBACK",
                    "lease": None,
                    "violations": ["CLOCK_ROLLBACK_DETECTED_AT_CLAIM"],
                }

            cap_err = self._check_capacity_in_tx(conn)
            if cap_err:
                conn.execute("ROLLBACK;")
                return {"claimed": False, "status": "BLOCKED_LEDGER_CAPACITY", "lease": None, "violations": [cap_err]}

            existing = conn.execute(
                "SELECT grant_payload_digest, plan_digest, state, session_id FROM grants WHERE grant_identity_key = ?",
                (verified_grant.grant_identity_key,),
            ).fetchone()
            if existing is not None:
                conn.execute("ROLLBACK;")
                if str(existing["grant_payload_digest"]) != verified_grant.grant_payload_digest:
                    return {
                        "claimed": False,
                        "status": "REJECTED_GRANT_PAYLOAD_CONFLICT",
                        "lease": None,
                        "violations": ["SAME_GRANT_IDENTITY_DIFFERENT_PAYLOAD_DENIED"],
                    }
                if str(existing["plan_digest"]) != verified_plan_digest:
                    return {
                        "claimed": False,
                        "status": "REJECTED_GRANT_REPLAN_REPLAY",
                        "lease": None,
                        "violations": [f"GRANT_ALREADY_CONSUMED_NO_REPLAN:state={existing['state']}"],
                    }
                return {
                    "claimed": False,
                    "status": "REJECTED_GRANT_ALREADY_CLAIMED_OR_SPENT",
                    "lease": None,
                    "violations": [f"ONE_SHOT_GRANT_REPLAY_DENIED:state={existing['state']}:prior_session={existing['session_id']}"],
                }

            zero_j = json.dumps(_zero_budget_dict(), sort_keys=True, separators=(",", ":"))
            caps_j = json.dumps(dict(verified_grant.caps), sort_keys=True, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO grants (
                    grant_identity_key, grant_id, issuer, trust_namespace,
                    grant_payload_digest, plan_digest, session_id, stage, state,
                    expires_utc_ms, claimed_utc_ms, caps_json, known_json,
                    uncertain_json, outstanding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?, ?, ?, ?)
                """,
                (
                    verified_grant.grant_identity_key,
                    verified_grant.grant_id,
                    verified_grant.issuer,
                    verified_grant.trust_namespace,
                    verified_grant.grant_payload_digest,
                    verified_plan_digest,
                    session_id,
                    verified_grant.stage,
                    verified_grant.expires_utc_ms,
                    now_ms,
                    caps_j,
                    zero_j,
                    zero_j,
                    zero_j,
                ),
            )
            new_meta = self._append_audit_in_tx(
                conn,
                event_type="GRANT_CLAIMED",
                event_utc_ms=now_ms,
                payload={
                    "grant_identity_key": verified_grant.grant_identity_key,
                    "grant_payload_digest": verified_grant.grant_payload_digest,
                    "plan_digest": verified_plan_digest,
                    "session_id": session_id,
                    "stage": verified_grant.stage,
                },
            )
            if crash_hook is not None:
                crash_hook("pre_claim_commit")
            conn.execute("COMMIT;")
            if crash_hook is not None:
                crash_hook("post_claim_pre_anchor")
            self._sync_anchor_from_meta(new_meta)
            if crash_hook is not None:
                crash_hook("post_claim_post_anchor")

            lease = R6DurableSessionLease(
                lease_id=f"lease_{session_id}_{new_meta['generation']}",
                grant_identity_key=verified_grant.grant_identity_key,
                grant_payload_digest=verified_grant.grant_payload_digest,
                plan_digest=verified_plan_digest,
                session_id=session_id,
                store_id=self.store_id,
                trust_namespace=self.trust_namespace,
                stage=verified_grant.stage,
                expires_utc_ms=verified_grant.expires_utc_ms,
                claimed_generation=int(new_meta["generation"]),
                external_capture_permitted=verified_grant.external_capture_permitted,
            )
            return {"claimed": True, "status": "CLAIMED_ONE_SHOT", "lease": lease, "violations": []}
        except sqlite3.OperationalError as exc:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            return {"claimed": False, "status": "BLOCKED_LEDGER_LOCK_OR_IO_ERROR", "lease": None, "violations": [f"SQLITE_ERROR:{exc}"]}
        finally:
            conn.close()

    # F10: Persistent pre-dispatch I/O budget reservation
    def reserve_r6_io_budget_atomic(
        self,
        lease: R6DurableSessionLease,
        attempt_id: str,
        request_type: str,
        requested_caps: Mapping[str, int],
        *,
        actual_now: datetime,
        crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(lease, R6DurableSessionLease) or lease.store_id != self.store_id:
            return {"reserved": False, "status": "REJECTED_INVALID_LEASE", "violations": ["INVALID_OR_FOREIGN_LEASE"]}
        if not str(attempt_id or "").strip() or not str(request_type or "").strip():
            return {"reserved": False, "status": "REJECTED_INVALID_ATTEMPT", "violations": ["MISSING_ATTEMPT_OR_TYPE"]}

        # Validate non-negative strict ints & zero overflow
        req_vec = _zero_budget_dict()
        for k, v in requested_caps.items():
            if k not in req_vec:
                return {"reserved": False, "status": "REJECTED_UNKNOWN_BUDGET_KEY", "violations": [f"UNKNOWN_BUDGET_KEY:{k}"]}
            if not _is_strict_int(v) or int(v) < 0 or int(v) > MAX_INT64_SAFE_R6:
                return {"reserved": False, "status": "REJECTED_INVALID_BUDGET_VALUE", "violations": [f"INVALID_BUDGET_VALUE:{k}:{v!r}"]}
            req_vec[k] = int(v)

        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        if now_ms >= lease.expires_utc_ms:
            return {"reserved": False, "status": "REJECTED_LEASE_EXPIRED", "violations": ["LEASE_EXPIRED_BEFORE_RESERVE"]}

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
            if int(meta["quarantined"]) != 0:
                conn.execute("ROLLBACK;")
                return {"reserved": False, "status": "BLOCKED_LEDGER_QUARANTINED", "violations": [f"LEDGER_QUARANTINED:{meta['quarantine_reason']}"]}

            grow = conn.execute("SELECT * FROM grants WHERE grant_identity_key = ?", (lease.grant_identity_key,)).fetchone()
            if grow is None or str(grow["session_id"]) != lease.session_id or str(grow["state"]) != "CLAIMED":
                conn.execute("ROLLBACK;")
                return {
                    "reserved": False,
                    "status": "REJECTED_GRANT_NOT_ACTIVE",
                    "violations": [f"GRANT_NOT_CLAIMED_FOR_SESSION:state={grow['state'] if grow else 'MISSING'}"],
                }
            if now_ms >= int(grow["expires_utc_ms"]):
                conn.execute("ROLLBACK;")
                return {"reserved": False, "status": "REJECTED_GRANT_EXPIRED", "violations": ["GRANT_EXPIRED_IN_LEDGER"]}

            dup_att = conn.execute("SELECT state FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if dup_att is not None:
                conn.execute("ROLLBACK;")
                return {"reserved": False, "status": "REJECTED_DUPLICATE_ATTEMPT_ID", "violations": [f"DUPLICATE_ATTEMPT_ID:{attempt_id}"]}

            caps = json.loads(grow["caps_json"])
            known = json.loads(grow["known_json"])
            uncertain = json.loads(grow["uncertain_json"])
            outstanding = json.loads(grow["outstanding_json"])

            exceeded: List[str] = []
            new_out = dict(outstanding)
            for k in BUDGET_FIELDS_R6:
                projected = int(known[k]) + int(uncertain[k]) + int(outstanding[k]) + int(req_vec[k])
                if projected > MAX_INT64_SAFE_R6 or projected > int(caps[k]):
                    exceeded.append(f"BUDGET_EXCEEDED:{k}:projected={projected}>cap={caps[k]}")
                new_out[k] = int(outstanding[k]) + int(req_vec[k])

            if exceeded:
                conn.execute("ROLLBACK;")
                return {"reserved": False, "status": "REJECTED_BUDGET_EXHAUSTED", "violations": exceeded}

            zero_j = json.dumps(_zero_budget_dict(), sort_keys=True, separators=(",", ":"))
            req_j = json.dumps(req_vec, sort_keys=True, separators=(",", ":"))
            out_j = json.dumps(new_out, sort_keys=True, separators=(",", ":"))

            conn.execute(
                """
                INSERT INTO attempts (
                    attempt_id, grant_identity_key, session_id, request_type, state,
                    reserved_json, known_json, uncertain_json, created_utc_ms
                ) VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
                """,
                (attempt_id, lease.grant_identity_key, lease.session_id, request_type, req_j, zero_j, zero_j, now_ms),
            )
            conn.execute(
                "UPDATE grants SET outstanding_json = ? WHERE grant_identity_key = ?",
                (out_j, lease.grant_identity_key),
            )
            new_meta = self._append_audit_in_tx(
                conn,
                event_type="BUDGET_RESERVED",
                event_utc_ms=now_ms,
                payload={"grant_identity_key": lease.grant_identity_key, "attempt_id": attempt_id, "reserved": req_vec},
            )
            conn.execute("COMMIT;")
            if crash_hook is not None:
                crash_hook("post_reserve_pre_anchor")
            self._sync_anchor_from_meta(new_meta)
            if crash_hook is not None:
                crash_hook("post_reserve_pre_start")
            return {"reserved": True, "status": "RESERVED_QUEUED", "attempt_id": attempt_id, "reserved_budget": req_vec, "violations": []}
        finally:
            conn.close()

    # F11a: Mark attempt started durably BEFORE external I/O
    def mark_r6_attempt_started(
        self,
        lease: R6DurableSessionLease,
        attempt_id: str,
        *,
        actual_now: datetime,
        crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        if now_ms >= lease.expires_utc_ms:
            return {"started": False, "status": "REJECTED_LEASE_EXPIRED", "violations": ["LEASE_EXPIRED_AT_START"]}
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            arow = conn.execute(
                "SELECT state, grant_identity_key, session_id FROM attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if arow is None or str(arow["grant_identity_key"]) != lease.grant_identity_key or str(arow["session_id"]) != lease.session_id:
                conn.execute("ROLLBACK;")
                return {"started": False, "status": "REJECTED_UNKNOWN_ATTEMPT", "violations": ["ATTEMPT_NOT_FOUND_FOR_LEASE"]}
            if str(arow["state"]) != "QUEUED":
                conn.execute("ROLLBACK;")
                return {"started": False, "status": "REJECTED_INVALID_TRANSITION", "violations": [f"ATTEMPT_NOT_QUEUED:{arow['state']}"]}

            conn.execute(
                "UPDATE attempts SET state = 'STARTED', started_utc_ms = ? WHERE attempt_id = ?",
                (now_ms, attempt_id),
            )
            new_meta = self._append_audit_in_tx(
                conn,
                event_type="ATTEMPT_STARTED",
                event_utc_ms=now_ms,
                payload={"grant_identity_key": lease.grant_identity_key, "attempt_id": attempt_id},
            )
            conn.execute("COMMIT;")
            if crash_hook is not None:
                crash_hook("post_start_pre_anchor")
            self._sync_anchor_from_meta(new_meta)
            if crash_hook is not None:
                crash_hook("post_start_pre_settle")
            return {"started": True, "status": "STARTED", "attempt_id": attempt_id, "violations": []}
        finally:
            conn.close()

    # F11b: Settle attempt with known actuals + conservative uncertain retention (no double settle!)
    def settle_r6_io_budget(
        self,
        lease: R6DurableSessionLease,
        attempt_id: str,
        *,
        outcome: str,  # "COMPLETED" | "UNCERTAIN" | "ABORTED_PRE_START" | "ABORTED_MIDFLIGHT"
        actual_known: Optional[Mapping[str, int]] = None,
        uncertain_retained: Optional[Mapping[str, int]] = None,
        reason: str = "",
        actual_now: datetime,
        crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        allowed_outcomes = ("COMPLETED", "UNCERTAIN", "ABORTED_PRE_START", "ABORTED_MIDFLIGHT")
        if outcome not in allowed_outcomes:
            return {"settled": False, "status": "REJECTED_INVALID_OUTCOME", "violations": [f"INVALID_OUTCOME:{outcome}"]}

        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            arow = conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if arow is None or str(arow["grant_identity_key"]) != lease.grant_identity_key or str(arow["session_id"]) != lease.session_id:
                conn.execute("ROLLBACK;")
                return {"settled": False, "status": "REJECTED_UNKNOWN_ATTEMPT", "violations": ["ATTEMPT_NOT_FOUND_FOR_LEASE"]}

            cur_state = str(arow["state"])
            if cur_state not in ("QUEUED", "STARTED"):
                conn.execute("ROLLBACK;")
                return {"settled": False, "status": "REJECTED_DOUBLE_SETTLE", "violations": [f"ATTEMPT_ALREADY_SETTLED:{cur_state}"]}

            if outcome == "ABORTED_PRE_START" and cur_state != "QUEUED":
                conn.execute("ROLLBACK;")
                return {"settled": False, "status": "REJECTED_PRE_START_RELEASE_AFTER_START", "violations": ["CANNOT_RELEASE_ALREADY_STARTED_ATTEMPT"]}
            if outcome in ("COMPLETED", "UNCERTAIN", "ABORTED_MIDFLIGHT") and cur_state != "STARTED":
                conn.execute("ROLLBACK;")
                return {"settled": False, "status": "REJECTED_SETTLE_UNSTARTED_ATTEMPT", "violations": [f"CANNOT_SETTLE_{outcome}_FROM_{cur_state}"]}

            reserved = json.loads(arow["reserved_json"])
            known_vec = _zero_budget_dict()
            unc_vec = _zero_budget_dict()

            if outcome == "ABORTED_PRE_START":
                # Demonstrably never started: release reservation cleanly (known=0, uncertain=0)
                pass
            elif outcome in ("UNCERTAIN", "ABORTED_MIDFLIGHT"):
                # Conservative retention: any unmeasured remainder of reserved worst-case stays in uncertain!
                for k in BUDGET_FIELDS_R6:
                    kv = (actual_known or {}).get(k, 0)
                    if not _is_strict_int(kv) or int(kv) < 0:
                        conn.execute("ROLLBACK;")
                        return {"settled": False, "status": "REJECTED_NEGATIVE_ACTUAL", "violations": [f"INVALID_KNOWN_VALUE:{k}"]}
                    known_vec[k] = int(kv)
                    default_unc = max(0, int(reserved[k]) - known_vec[k])
                    uv = (uncertain_retained or {}).get(k, default_unc)
                    if not _is_strict_int(uv) or int(uv) < 0:
                        conn.execute("ROLLBACK;")
                        return {"settled": False, "status": "REJECTED_NEGATIVE_UNCERTAIN", "violations": [f"INVALID_UNCERTAIN_VALUE:{k}"]}
                    # Enforce conservative lower bound: cannot under-report uncertain below reserved - known on midflight abort!
                    unc_vec[k] = max(int(uv), default_unc)
            else:
                # COMPLETED: record exact actual known; any explicit uncertain also retained
                for k in BUDGET_FIELDS_R6:
                    kv = (actual_known or {}).get(k, 0)
                    uv = (uncertain_retained or {}).get(k, 0)
                    if not _is_strict_int(kv) or int(kv) < 0 or not _is_strict_int(uv) or int(uv) < 0:
                        conn.execute("ROLLBACK;")
                        return {"settled": False, "status": "REJECTED_NEGATIVE_ACTUAL", "violations": [f"NEGATIVE_SETTLE_VALUE:{k}"]}
                    known_vec[k] = int(kv)
                    unc_vec[k] = int(uv)

            grow = conn.execute("SELECT known_json, uncertain_json, outstanding_json FROM grants WHERE grant_identity_key = ?", (lease.grant_identity_key,)).fetchone()
            g_known = json.loads(grow["known_json"])
            g_unc = json.loads(grow["uncertain_json"])
            g_out = json.loads(grow["outstanding_json"])

            for k in BUDGET_FIELDS_R6:
                new_out = int(g_out[k]) - int(reserved[k])
                if new_out < 0:
                    conn.execute("ROLLBACK;")
                    return {"settled": False, "status": "REJECTED_NEGATIVE_COUNTER", "violations": [f"OUTSTANDING_UNDERFLOW:{k}"]}
                g_out[k] = new_out
                g_known[k] = int(g_known[k]) + known_vec[k]
                g_unc[k] = int(g_unc[k]) + unc_vec[k]

            final_state = "ABORTED" if outcome.startswith("ABORTED") else outcome
            conn.execute(
                """
                UPDATE attempts
                SET state = ?, known_json = ?, uncertain_json = ?, settled_utc_ms = ?, outcome_reason = ?
                WHERE attempt_id = ?
                """,
                (
                    final_state,
                    json.dumps(known_vec, sort_keys=True, separators=(",", ":")),
                    json.dumps(unc_vec, sort_keys=True, separators=(",", ":")),
                    now_ms,
                    str(reason or outcome),
                    attempt_id,
                ),
            )
            conn.execute(
                "UPDATE grants SET known_json = ?, uncertain_json = ?, outstanding_json = ? WHERE grant_identity_key = ?",
                (
                    json.dumps(g_known, sort_keys=True, separators=(",", ":")),
                    json.dumps(g_unc, sort_keys=True, separators=(",", ":")),
                    json.dumps(g_out, sort_keys=True, separators=(",", ":")),
                    lease.grant_identity_key,
                ),
            )
            new_meta = self._append_audit_in_tx(
                conn,
                event_type="ATTEMPT_SETTLED",
                event_utc_ms=now_ms,
                payload={
                    "grant_identity_key": lease.grant_identity_key,
                    "attempt_id": attempt_id,
                    "outcome": outcome,
                    "known": known_vec,
                    "uncertain": unc_vec,
                },
            )
            conn.execute("COMMIT;")
            if crash_hook is not None:
                crash_hook("post_settle_pre_anchor")
            self._sync_anchor_from_meta(new_meta)
            return {
                "settled": True,
                "status": final_state,
                "known": known_vec,
                "uncertain": unc_vec,
                "violations": [],
            }
        finally:
            conn.close()

    # F11c: Finalize one-shot grant as SPENT or QUARANTINED
    def finalize_r6_session(
        self,
        lease: R6DurableSessionLease,
        *,
        quarantine: bool = False,
        reason: str = "SESSION_COMPLETE",
        actual_now: datetime,
        crash_hook: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            grow = conn.execute("SELECT * FROM grants WHERE grant_identity_key = ?", (lease.grant_identity_key,)).fetchone()
            if grow is None or str(grow["session_id"]) != lease.session_id:
                conn.execute("ROLLBACK;")
                return {"finalized": False, "status": "REJECTED_UNKNOWN_SESSION", "violations": ["GRANT_SESSION_NOT_FOUND"]}

            if str(grow["state"]) in ("SPENT", "QUARANTINED", "SPENT_CRASH_RECOVERED"):
                # Idempotent audit check: never un-spend!
                conn.execute("ROLLBACK;")
                return {"finalized": True, "status": str(grow["state"]), "idempotent_already_finalized": True, "violations": []}

            # Convert any unsettled STARTED attempts conservatively to UNCERTAIN; QUEUED to ABORTED
            g_unc = json.loads(grow["uncertain_json"])
            g_out = json.loads(grow["outstanding_json"])
            open_atts = conn.execute(
                "SELECT * FROM attempts WHERE grant_identity_key = ? AND state IN ('QUEUED', 'STARTED')",
                (lease.grant_identity_key,),
            ).fetchall()
            for arow in open_atts:
                res = json.loads(arow["reserved_json"])
                if str(arow["state"]) == "STARTED":
                    for k in BUDGET_FIELDS_R6:
                        g_unc[k] = int(g_unc[k]) + int(res[k])
                        g_out[k] = max(0, int(g_out[k]) - int(res[k]))
                    conn.execute(
                        "UPDATE attempts SET state = 'UNCERTAIN', uncertain_json = ?, settled_utc_ms = ?, outcome_reason = 'FINALIZE_UNSETTLED_STARTED' WHERE attempt_id = ?",
                        (arow["reserved_json"], now_ms, arow["attempt_id"]),
                    )
                else:
                    for k in BUDGET_FIELDS_R6:
                        g_out[k] = max(0, int(g_out[k]) - int(res[k]))
                    conn.execute(
                        "UPDATE attempts SET state = 'ABORTED', settled_utc_ms = ?, outcome_reason = 'FINALIZE_UNSTARTED_QUEUED' WHERE attempt_id = ?",
                        (now_ms, arow["attempt_id"]),
                    )

            final_state = "QUARANTINED" if quarantine else "SPENT"
            conn.execute(
                """
                UPDATE grants
                SET state = ?, finalized_utc_ms = ?, uncertain_json = ?, outstanding_json = ?, final_reason = ?
                WHERE grant_identity_key = ?
                """,
                (
                    final_state,
                    now_ms,
                    json.dumps(g_unc, sort_keys=True, separators=(",", ":")),
                    json.dumps(g_out, sort_keys=True, separators=(",", ":")),
                    str(reason),
                    lease.grant_identity_key,
                ),
            )
            new_meta = self._append_audit_in_tx(
                conn,
                event_type="SESSION_FINALIZED",
                event_utc_ms=now_ms,
                payload={"grant_identity_key": lease.grant_identity_key, "session_id": lease.session_id, "final_state": final_state, "reason": reason},
                set_quarantine=(reason if quarantine else None),
            )
            conn.execute("COMMIT;")
            if crash_hook is not None:
                crash_hook("post_finalize_pre_anchor")
            self._sync_anchor_from_meta(new_meta)
            return {"finalized": True, "status": final_state, "idempotent_already_finalized": False, "violations": []}
        finally:
            conn.close()

    def snapshot_grant(self, grant_identity_key: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            grow = conn.execute("SELECT * FROM grants WHERE grant_identity_key = ?", (grant_identity_key,)).fetchone()
            if grow is None:
                return None
            atts = conn.execute("SELECT * FROM attempts WHERE grant_identity_key = ? ORDER BY created_utc_ms, attempt_id", (grant_identity_key,)).fetchall()
            return {
                "grant_identity_key": grow["grant_identity_key"],
                "grant_id": grow["grant_id"],
                "state": grow["state"],
                "session_id": grow["session_id"],
                "stage": grow["stage"],
                "caps": json.loads(grow["caps_json"]),
                "known": json.loads(grow["known_json"]),
                "uncertain": json.loads(grow["uncertain_json"]),
                "outstanding": json.loads(grow["outstanding_json"]),
                "final_reason": grow["final_reason"],
                "attempts": [
                    {
                        "attempt_id": a["attempt_id"],
                        "request_type": a["request_type"],
                        "state": a["state"],
                        "reserved": json.loads(a["reserved_json"]),
                        "known": json.loads(a["known_json"]),
                        "uncertain": json.loads(a["uncertain_json"]),
                        "outcome_reason": a["outcome_reason"],
                    }
                    for a in atts
                ],
            }
        finally:
            conn.close()

    # F12: Crash / restart recovery for unfinished CLAIMED grants & STARTED attempts
    def recover_r6_ledger_after_restart(self, *, actual_now: datetime) -> Dict[str, Any]:
        """
        Recovers any unfinished sessions after an abrupt process crash:
          - STARTED attempts -> UNCERTAIN (retaining 100% of reserved worst-case bytes/requests, ZERO auto-refund!)
          - QUEUED attempts -> ABORTED (never started; grant still stays SPENT_CRASH_RECOVERED!)
          - CLAIMED grants -> SPENT_CRASH_RECOVERED (one-shot replay permanently denied!)
        """
        now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
        recovered_grants: List[str] = []
        recovered_started_attempts: List[str] = []
        recovered_queued_attempts: List[str] = []

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
            if int(meta["quarantined"]) != 0:
                conn.execute("ROLLBACK;")
                return {
                    "recovered": False,
                    "status": "BLOCKED_LEDGER_QUARANTINED",
                    "recovered_grants": [],
                    "recovered_started_attempts": [],
                    "recovered_queued_attempts": [],
                    "violations": [f"LEDGER_QUARANTINED:{meta['quarantine_reason']}"],
                }

            claimed_rows = conn.execute("SELECT * FROM grants WHERE state = 'CLAIMED'").fetchall()
            for grow in claimed_rows:
                gkey = str(grow["grant_identity_key"])
                g_unc = json.loads(grow["uncertain_json"])
                g_out = json.loads(grow["outstanding_json"])

                atts = conn.execute(
                    "SELECT * FROM attempts WHERE grant_identity_key = ? AND state IN ('QUEUED', 'STARTED')",
                    (gkey,),
                ).fetchall()
                for arow in atts:
                    res = json.loads(arow["reserved_json"])
                    aid = str(arow["attempt_id"])
                    if str(arow["state"]) == "STARTED":
                        for k in BUDGET_FIELDS_R6:
                            g_unc[k] = int(g_unc[k]) + int(res[k])
                            g_out[k] = max(0, int(g_out[k]) - int(res[k]))
                        conn.execute(
                            "UPDATE attempts SET state = 'UNCERTAIN', uncertain_json = ?, settled_utc_ms = ?, outcome_reason = 'CRASH_RECOVERED_UNKNOWN_RETAINED' WHERE attempt_id = ?",
                            (arow["reserved_json"], now_ms, aid),
                        )
                        recovered_started_attempts.append(aid)
                    else:
                        for k in BUDGET_FIELDS_R6:
                            g_out[k] = max(0, int(g_out[k]) - int(res[k]))
                        conn.execute(
                            "UPDATE attempts SET state = 'ABORTED', settled_utc_ms = ?, outcome_reason = 'CRASH_RECOVERED_PRE_START' WHERE attempt_id = ?",
                            (now_ms, aid),
                        )
                        recovered_queued_attempts.append(aid)

                conn.execute(
                    """
                    UPDATE grants
                    SET state = 'SPENT_CRASH_RECOVERED', finalized_utc_ms = ?, uncertain_json = ?, outstanding_json = ?, final_reason = 'CRASH_RECOVERED_ONE_SHOT_SPENT'
                    WHERE grant_identity_key = ?
                    """,
                    (
                        now_ms,
                        json.dumps(g_unc, sort_keys=True, separators=(",", ":")),
                        json.dumps(g_out, sort_keys=True, separators=(",", ":")),
                        gkey,
                    ),
                )
                recovered_grants.append(gkey)

            if recovered_grants:
                new_meta = self._append_audit_in_tx(
                    conn,
                    event_type="RESTART_CRASH_RECOVERY",
                    event_utc_ms=now_ms,
                    payload={
                        "recovered_grants": recovered_grants,
                        "recovered_started_attempts": recovered_started_attempts,
                        "recovered_queued_attempts": recovered_queued_attempts,
                    },
                )
                conn.execute("COMMIT;")
                self._sync_anchor_from_meta(new_meta)
            else:
                conn.execute("ROLLBACK;")

            return {
                "recovered": True,
                "status": "RECOVERED_GRANTS_MARKED_SPENT" if recovered_grants else "CLEAN_NO_INTERRUPTED_SESSIONS",
                "recovered_grants": recovered_grants,
                "recovered_started_attempts": recovered_started_attempts,
                "recovered_queued_attempts": recovered_queued_attempts,
                "violations": [],
            }
        finally:
            conn.close()


# =====================================================================
# F08: open_r6_durable_ledger + F12 read-only audit helper
# =====================================================================
def _validate_db_and_anchor(
    store_root: Path,
    *,
    expected_store_id: str,
    expected_trust_namespace: str,
    repo_root: Optional[Path] = None,
    actual_now: Optional[datetime] = None,
    allow_forward_anchor_recovery: bool = True,
) -> Dict[str, Any]:
    viols = _check_local_path_safety(store_root, repo_root=repo_root, must_exist=True)
    if viols:
        return {"ok": False, "status": "BLOCKED_LEDGER_PATH_UNSAFE_OR_MISSING", "violations": viols}

    db_path = store_root / "ledger.sqlite3"
    anchor_path = store_root / "ledger.anchor.json"
    if not db_path.is_file():
        return {"ok": False, "status": "BLOCKED_LEDGER_DB_MISSING_NO_AUTO_INIT", "violations": ["LEDGER_DB_MISSING"]}
    if not anchor_path.is_file():
        return {"ok": False, "status": "BLOCKED_LEDGER_ANCHOR_MISSING", "violations": ["LEDGER_ANCHOR_MISSING"]}
    if _is_reparse_or_symlink(db_path) or _is_reparse_or_symlink(anchor_path):
        return {"ok": False, "status": "BLOCKED_LEDGER_SYMLINK_FORBIDDEN", "violations": ["LEDGER_FILE_SYMLINK_FORBIDDEN"]}

    # Parse and verify external anchor
    try:
        anchor = _strict_loads_r6(anchor_path.read_bytes(), max_bytes=32_768)
    except Exception as exc:
        return {"ok": False, "status": "QUARANTINED_ANCHOR_CORRUPT", "violations": [f"ANCHOR_PARSE_ERROR:{exc}"]}

    if not isinstance(anchor, Mapping):
        return {"ok": False, "status": "QUARANTINED_ANCHOR_CORRUPT", "violations": ["ANCHOR_NOT_OBJECT"]}

    anc_copy = {k: v for k, v in anchor.items() if k != "anchor_sha256"}
    if str(anchor.get("anchor_sha256") or "") != _sha256_canonical(anc_copy):
        return {"ok": False, "status": "QUARANTINED_ANCHOR_TAMPERED", "violations": ["ANCHOR_SHA256_MISMATCH"]}
    if str(anchor.get("store_id") or "") != expected_store_id:
        return {"ok": False, "status": "QUARANTINED_STORE_ID_MISMATCH", "violations": ["ANCHOR_STORE_ID_MISMATCH"]}
    if str(anchor.get("trust_namespace") or "") != expected_trust_namespace:
        return {"ok": False, "status": "QUARANTINED_NAMESPACE_MISMATCH", "violations": ["ANCHOR_TRUST_NAMESPACE_MISMATCH"]}

    # Open SQLite read-only first to verify integrity & audit chain without modifying corrupt file
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        return {"ok": False, "status": "QUARANTINED_DB_UNOPENABLE", "violations": [f"SQLITE_OPEN_ERROR:{exc}"]}

    try:
        qc = conn.execute("PRAGMA quick_check;").fetchone()
        if qc is None or str(qc[0]).lower() != "ok":
            return {"ok": False, "status": "QUARANTINED_DB_CORRUPT", "violations": [f"SQLITE_QUICK_CHECK_FAILED:{qc[0] if qc else None}"]}

        meta = conn.execute("SELECT * FROM ledger_meta WHERE id = 1").fetchone()
        if meta is None:
            return {"ok": False, "status": "QUARANTINED_DB_CORRUPT", "violations": ["MISSING_LEDGER_META_ROW"]}
        if str(meta["schema_version"]) != LEDGER_SCHEMA_VERSION_R6:
            return {"ok": False, "status": "BLOCKED_SCHEMA_MISMATCH", "violations": [f"SCHEMA_MISMATCH:{meta['schema_version']}"]}
        if str(meta["store_id"]) != expected_store_id:
            return {"ok": False, "status": "QUARANTINED_DB_REPLACED", "violations": ["DB_STORE_ID_MISMATCH"]}
        if str(meta["trust_namespace"]) != expected_trust_namespace:
            return {"ok": False, "status": "QUARANTINED_DB_NAMESPACE_MISMATCH", "violations": ["DB_TRUST_NAMESPACE_MISMATCH"]}

        # Recompute full audit chain from seq=1
        rows = conn.execute("SELECT * FROM audit_chain ORDER BY generation ASC").fetchall()
        if not rows:
            return {"ok": False, "status": "QUARANTINED_EMPTY_AUDIT_CHAIN", "violations": ["EMPTY_AUDIT_CHAIN"]}

        prev = "0" * 64
        expected_gen = 1
        last_prev = prev
        for r in rows:
            gen = int(r["generation"])
            if gen != expected_gen:
                return {"ok": False, "status": "QUARANTINED_AUDIT_GAP", "violations": [f"AUDIT_GEN_GAP:expected={expected_gen}:got={gen}"]}
            if str(r["prev_hash"]) != prev:
                return {"ok": False, "status": "QUARANTINED_AUDIT_CHAIN_BROKEN", "violations": [f"AUDIT_PREV_MISMATCH:gen={gen}"]}
            calc = _compute_audit_entry_hash(gen, str(r["event_type"]), int(r["event_utc_ms"]), str(r["payload_json"]), prev)
            if calc != str(r["entry_hash"]):
                return {"ok": False, "status": "QUARANTINED_AUDIT_HASH_TAMPERED", "violations": [f"AUDIT_HASH_MISMATCH:gen={gen}"]}
            last_prev = prev
            prev = calc
            expected_gen += 1

        db_gen = int(meta["generation"])
        db_tip = str(meta["audit_tip_hash"])
        if db_gen != expected_gen - 1 or db_tip != prev:
            return {"ok": False, "status": "QUARANTINED_META_TIP_MISMATCH", "violations": ["META_GEN_OR_TIP_MISMATCH_AUDIT_CHAIN"]}

        anc_gen = int(anchor["generation"])
        anc_tip = str(anchor["audit_tip_hash"])
        recovery_action = "NONE_EXACT_MATCH"

        if db_gen < anc_gen:
            return {
                "ok": False,
                "status": "QUARANTINED_ROLLBACK_DETECTED",
                "violations": [f"DB_GENERATION_BEHIND_ANCHOR:db={db_gen}<anchor={anc_gen}"],
            }
        elif db_gen == anc_gen:
            if db_tip != anc_tip:
                return {
                    "ok": False,
                    "status": "QUARANTINED_DB_ANCHOR_DIVERGENCE",
                    "violations": ["DB_TIP_MISMATCH_AT_SAME_GENERATION"],
                }
        elif db_gen == anc_gen + 1 and last_prev == anc_tip:
            # Deterministic crash window: SQLite COMMIT succeeded strictly before anchor os.replace!
            if allow_forward_anchor_recovery:
                recovery_action = "FORWARD_SYNC_ANCHOR_AFTER_COMMIT_CRASH"
            else:
                recovery_action = "PENDING_FORWARD_ANCHOR_SYNC_READONLY_OBSERVED"
        else:
            return {
                "ok": False,
                "status": "QUARANTINED_AMBIGUOUS_ANCHOR_LAG",
                "violations": [f"AMBIGUOUS_DB_ANCHOR_DELTA:db={db_gen}:anchor={anc_gen}"],
            }

        hw_ms = max(int(meta["high_water_utc_ms"]), int(anchor["high_water_utc_ms"]))
        if actual_now is not None:
            now_ms = int(actual_now.astimezone(timezone.utc).timestamp() * 1000)
            if now_ms + MAX_CLOCK_SKEW_MS_R6 < hw_ms:
                return {
                    "ok": False,
                    "status": "BLOCKED_CLOCK_ROLLBACK",
                    "violations": [f"CLOCK_ROLLBACK:now={now_ms}<hw={hw_ms}"],
                }

        meta_dict = dict(meta)
        grants_count = int(conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0])
        attempts_count = int(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "status": "QUARANTINED_DB_CORRUPT", "violations": [f"SQLITE_CORRUPT:{exc}"]}
    finally:
        conn.close()

    if recovery_action == "FORWARD_SYNC_ANCHOR_AFTER_COMMIT_CRASH":
        synced = _compute_anchor_doc(
            store_id=str(meta_dict["store_id"]),
            trust_namespace=str(meta_dict["trust_namespace"]),
            generation=int(meta_dict["generation"]),
            audit_tip_hash=str(meta_dict["audit_tip_hash"]),
            high_water_utc_ms=int(meta_dict["high_water_utc_ms"]),
            quarantined=bool(meta_dict["quarantined"]),
            quarantine_reason=str(meta_dict["quarantine_reason"]),
        )
        _atomic_write_json_fsync(anchor_path, synced)

    if bool(meta_dict["quarantined"]):
        return {
            "ok": False,
            "status": "BLOCKED_LEDGER_QUARANTINED",
            "violations": [f"LEDGER_QUARANTINED:{meta_dict['quarantine_reason']}"],
            "meta": meta_dict,
            "recovery_action": recovery_action,
            "grants_count": grants_count,
            "attempts_count": attempts_count,
        }

    return {
        "ok": True,
        "status": "LEDGER_OPEN_VERIFIED",
        "violations": [],
        "db_path": db_path,
        "anchor_path": anchor_path,
        "meta": meta_dict,
        "recovery_action": recovery_action,
        "grants_count": grants_count,
        "attempts_count": attempts_count,
    }


def open_r6_durable_ledger(
    owner_config: Mapping[str, Any],
    expected_store_id: str,
    *,
    expected_trust_namespace: str = OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    repo_root: Optional[Path] = None,
    actual_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Opens an explicitly provisioned R6 durable SQLite ledger + external anchor.
    NEVER auto-initializes a missing database or resets a corrupt/rolled-back database!
    """
    raw_root = owner_config.get("ledger_store_root")
    if not raw_root or not str(raw_root).strip():
        return {"opened": False, "status": "BLOCKED_LEDGER_NOT_CONFIGURED", "ledger": None, "violations": ["LEDGER_STORE_ROOT_NOT_CONFIGURED"]}
    cfg_store_id = str(owner_config.get("store_id") or "").strip()
    if cfg_store_id != expected_store_id:
        return {"opened": False, "status": "BLOCKED_STORE_ID_MISMATCH", "ledger": None, "violations": ["OWNER_CONFIG_STORE_ID_MISMATCH"]}

    store_root = Path(str(raw_root))
    check = _validate_db_and_anchor(
        store_root,
        expected_store_id=expected_store_id,
        expected_trust_namespace=expected_trust_namespace,
        repo_root=repo_root,
        actual_now=actual_now,
        allow_forward_anchor_recovery=True,
    )
    if not check["ok"]:
        return {
            "opened": False,
            "status": check["status"],
            "ledger": None,
            "violations": check["violations"],
            "recovery_action": check.get("recovery_action"),
        }

    meta = check["meta"]
    ledger = R6DurableGrantLedger(
        store_root=store_root.resolve(),
        db_path=check["db_path"],
        anchor_path=check["anchor_path"],
        store_id=expected_store_id,
        trust_namespace=expected_trust_namespace,
        max_grants=int(meta["max_grants"]),
        max_db_bytes=int(meta["max_db_bytes"]),
    )
    return {
        "opened": True,
        "status": "LEDGER_OPEN_VERIFIED",
        "ledger": ledger,
        "generation": int(meta["generation"]),
        "audit_tip_hash": str(meta["audit_tip_hash"]),
        "recovery_action": check["recovery_action"],
        "grants_count": check["grants_count"],
        "attempts_count": check["attempts_count"],
        "violations": [],
    }


def verify_r6_ledger_readonly(
    store_root: Path,
    *,
    expected_store_id: str,
    expected_trust_namespace: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Read-only independent audit of a durable R6 ledger & external anchor (never mutates files)."""
    res = _validate_db_and_anchor(
        store_root,
        expected_store_id=expected_store_id,
        expected_trust_namespace=expected_trust_namespace,
        repo_root=repo_root,
        actual_now=None,
        allow_forward_anchor_recovery=False,
    )
    return {
        "valid": bool(res["ok"]),
        "status": res["status"],
        "violations": list(res["violations"]),
        "generation": int(res["meta"]["generation"]) if res.get("meta") else None,
        "audit_tip_hash": str(res["meta"]["audit_tip_hash"]) if res.get("meta") else None,
        "grants_count": res.get("grants_count", 0),
        "attempts_count": res.get("attempts_count", 0),
        "recovery_action": res.get("recovery_action"),
    }
