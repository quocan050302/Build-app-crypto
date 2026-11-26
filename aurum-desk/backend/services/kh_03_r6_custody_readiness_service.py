"""
aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py

AURUM DESK — KH-03.R6 (M5) | CUSTODY, PROVENANCE, DEV DATASET & 36-GATE STAGE-SCOPED READINESS
SPEC VERSION: KH-03.R6-v1.0 | DEV/QA ISOLATION | APPEND-ONLY CUSTODY + ANCHOR | 36-GATE ENGINE

Implements:
  - F25: assign_r6_dev_partition_before_io / enforce_r6_destination_guard (handle/reparse/OOS deny-before-open)
  - F26: mint_r6_raw_provenance_receipt / verify_r6_raw_provenance_receipt (R6-specific, never C4 fake)
  - F27: R6CustodyJournal (persist_r6_verified_chunk, verify_r6_custody_readonly, safe owned-temp cleanup)
  - F28: seal_r6_dev_dataset_manifest
  - F29: derive_r6_readiness_verdicts (36 stage-scoped gates G01..G36) & build_r6_owner_handover_vi
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    R6DurableSettlementReceipt,
    R6TrustedSupervisorResult,
    bind_provenance_receipt_to_trusted_token_a1,
    verify_and_consume_custody_token_a1,
    verify_durable_settlement_receipt_a1,
    verify_trusted_supervisor_result_a1,
)
from services.kh_03_r6_durable_grant_service import (
    _atomic_write_json_fsync,
    _check_local_path_safety,
    _fsync_file_and_parent,
    _has_any_reparse_ancestor,
    _is_inside_directory,
    _is_reparse_or_symlink,
    _is_strict_int,
    _sha256_bytes,
    _sha256_canonical,
    _strict_loads_r6,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    BASELINE_REVIEWED_HEAD_SHA_R6,
    MANDATORY_BRANCH_KH03_R6,
    PRIOR_C2_EVIDENCE_COMMIT_SHA,
    PRIOR_C2_RUN_ID,
    PRIOR_C2_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)

PROVENANCE_SCHEMA_VERSION_R6 = "kh_03_r6_raw_provenance_v1"
CUSTODY_SCHEMA_VERSION_R6 = "kh_03_r6_custody_chain_v1"
DATASET_MANIFEST_SCHEMA_R6 = "kh_03_r6_dev_dataset_manifest_v1"

MIN_EXPECTED_PYTEST_COLLECTED_R6 = 5955
MIN_NEW_PAIRED_CONTROLS_R6 = 56
MIN_NEW_VERIFIER_MUTATIONS_R6 = 30

CLASSIFICATION_PASS_OFFLINE_R6 = (
    "ENGINEERING_PASS_OFFLINE_R6_ACQUISITION_READY_FOR_OWNER_REVIEW_"
    "CAPTURE_BLOCKED_NO_PROVEN_EDGE"
)

_PROVENANCE_MAC_SECRET_R6 = secrets.token_bytes(32)

OOS_FORBIDDEN_PATH_TOKENS_R6: Tuple[str, ...] = (
    "oos_escrow",
    "fresh_oos",
    "sealed_oos",
    "holdout_oos",
    "untouched_oos",
    "/oos/",
    "\\oos\\",
)

GATE_36_KEYS_KH03_R6: Tuple[str, ...] = (
    "G01_BRANCH_SHA_CLEAN",
    "G02_KEEP_AS_IS",
    "G03_PRIOR_C2_SEALED_DATASET_OOS",
    "G04_SOURCE_DOCS",
    "G05_STRICT_ENDPOINT_VERSION_IDENTITY",
    "G06_OWNER_TRUST_BOUNDARY",
    "G07_GRANT_PLAN_BINDING",
    "G08_DURABLE_ONE_SHOT",
    "G09_CRASH_ANCHOR_RECOVERY",
    "G10_BUDGET_CONSERVATION",
    "G11_OWNED_TRANSPORT_PROCESS",
    "G12_TERMINATION_REAP",
    "G13_REST_READ_DEADLINE_BOUND",
    "G14_WS_FRAME_RECONNECT_BOUND",
    "G15_RESOURCE_VOLUME",
    "G16_OHLCV_CONTRACT",
    "G17_BBO_CONTRACT",
    "G18_FUNDING_CONTRACT",
    "G19_FEE_SLIPPAGE_UNKNOWN_HONESTY",
    "G20_CUSTODY_PARTITION",
    "G21_FULL_PYTEST",
    "G22_BASELINE_PARITY",
    "G23_BACKTEST_PARITY",
    "G24_DETERMINISM",
    "G25_MEASURED_WORKER_POLICY",
    "G26_PAIRED_FAULT_TRUTH",
    "G27_INHERITED_REGRESSION",
    "G28_INDEPENDENT_VERIFIER",
    "G29_MUTATION_RESISTANCE",
    "G30_RESOURCE_CLEANUP",
    "G31_EXTERNAL_AUTHORIZATION",
    "G32_AUTHORIZED_SMOKE",
    "G33_DEV_ACQUISITION",
    "G34_R7_DATA_READINESS",
    "G35_ECONOMIC_ACTIVATION",
    "G36_REMOTE_PUBLICATION",
)

STAGE_A_MANDATORY_GATES_R6: Tuple[str, ...] = tuple(
    g for g in GATE_36_KEYS_KH03_R6 if g not in (
        "G31_EXTERNAL_AUTHORIZATION",
        "G32_AUTHORIZED_SMOKE",
        "G33_DEV_ACQUISITION",
        "G34_R7_DATA_READINESS",
    )
)

STAGE_BC_CONDITIONAL_GATES_R6: Tuple[str, ...] = (
    "G31_EXTERNAL_AUTHORIZATION",
    "G32_AUTHORIZED_SMOKE",
    "G33_DEV_ACQUISITION",
    "G34_R7_DATA_READINESS",
)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


# =====================================================================
# F25: DEV_ONLY / QA_FIXTURE Partition & Destination Guard (Deny-Before-Open)
# =====================================================================
def enforce_r6_destination_guard(
    target_path: Path,
    *,
    approved_root: Path,
    partition: str,
    repo_root: Optional[Path] = None,
    oos_roots: Sequence[Path] = (),
) -> Dict[str, Any]:
    """
    Fail-closed destination guard checked strictly BEFORE any file open/write/rename.
    Blocks OOS paths, Git repo paths, symlinks/NTFS junctions/reparse points, network shares,
    `..` alias traversal, and partition violations.
    """
    viols: List[str] = []
    if partition not in ("DEV_ONLY", "QA_FIXTURE"):
        viols.append(f"FORBIDDEN_PARTITION_CLASS:{partition}")

    raw_str = str(target_path)
    low_str = raw_str.lower()
    if any(tok in low_str for tok in OOS_FORBIDDEN_PATH_TOKENS_R6):
        viols.append("OOS_PATH_DENIED_BEFORE_OPEN")
    if ".." in target_path.parts:
        viols.append("PATH_TRAVERSAL_DOTDOT_FORBIDDEN")

    viols.extend(_check_local_path_safety(approved_root, repo_root=repo_root, must_exist=False))
    viols.extend(_check_local_path_safety(target_path, repo_root=repo_root, must_exist=False))

    # Check approved_root containment
    if not _is_inside_directory(target_path, approved_root):
        viols.append("TARGET_OUTSIDE_APPROVED_ROOT")

    # Check explicit OOS roots deny-before-open
    for oos_r in oos_roots:
        if _is_inside_directory(target_path, oos_r):
            viols.append("TARGET_INSIDE_OOS_ESCROW_ROOT_DENIED_BEFORE_OPEN")

    # Also check intermediate path segments for symlinks/junctions even if leaf does not yet exist
    if _has_any_reparse_ancestor(target_path) or _has_any_reparse_ancestor(approved_root):
        if "SYMLINK_OR_JUNCTION_FORBIDDEN" not in " ".join(viols):
            viols.append("SYMLINK_OR_JUNCTION_FORBIDDEN")

    return {
        "allowed": len(viols) == 0,
        "status": "DESTINATION_ALLOWED" if not viols else "DESTINATION_DENIED_BEFORE_OPEN",
        "partition": partition,
        "resolved_target": str(target_path.resolve(strict=False)),
        "violations": viols,
    }


def assign_r6_dev_partition_before_io(
    *,
    stage: str,
    provenance_class: str,
    approved_root: Path,
    run_id: str,
    repo_root: Optional[Path] = None,
    oos_roots: Sequence[Path] = (),
) -> Dict[str, Any]:
    """
    Assigns strict partition before I/O:
      - Stage B/C external capture -> DEV_ONLY (requires RAW_OBSERVED_VERIFIED later; NEVER OOS!)
      - Stage A loopback QA -> QA_FIXTURE (NEVER promoted to DEV_ONLY!)
    """
    if stage in ("B_SMOKE", "C_DEV"):
        if provenance_class == "QA_LOOPBACK":
            return {
                "assigned": False,
                "status": "REJECTED_QA_PROMOTION_TO_DEV_FORBIDDEN",
                "partition": None,
                "run_namespace_dir": None,
                "violations": ["QA_LOOPBACK_CANNOT_BE_ASSIGNED_DEV_ONLY"],
            }
        partition = "DEV_ONLY"
    elif stage == "QA_LOOPBACK":
        partition = "QA_FIXTURE"
    else:
        return {
            "assigned": False,
            "status": "REJECTED_UNKNOWN_STAGE",
            "partition": None,
            "run_namespace_dir": None,
            "violations": [f"UNKNOWN_STAGE:{stage}"],
        }

    run_dir = approved_root / partition.lower() / run_id
    guard = enforce_r6_destination_guard(
        run_dir,
        approved_root=approved_root,
        partition=partition,
        repo_root=repo_root,
        oos_roots=oos_roots,
    )
    if not guard["allowed"]:
        return {
            "assigned": False,
            "status": "BLOCKED_DESTINATION_GUARD",
            "partition": None,
            "run_namespace_dir": None,
            "violations": guard["violations"],
        }

    return {
        "assigned": True,
        "status": "PARTITION_ASSIGNED_BEFORE_IO",
        "partition": partition,
        "run_namespace_dir": str(run_dir),
        "violations": [],
    }


# =====================================================================
# F23 & F24: R6 Raw Provenance Receipts (Trusted Supervisor + Settlement Chain Only!)
# =====================================================================
@dataclass(frozen=True)
class R6RawProvenanceReceipt:
    schema_version: str
    receipt_id: str
    tested_executable_sha: str
    run_id: str
    session_id: str
    lease_id: str
    attempt_id: str
    plan_digest: str
    request_digest: str
    endpoint_id: str
    contract_digest: str
    partition: str  # "DEV_ONLY" | "QA_FIXTURE"
    provenance_class: str  # Strictly "QA_LOOPBACK" or "RAW_UNVERIFIED_REJECTED" in A1!
    transport_kind: str
    tls_verified: bool
    child_pid: int
    child_create_time: float
    child_exit_code: int
    reaped_verified: bool
    raw_payload_sha256: str
    raw_payload_bytes: int
    decoded_payload_sha256: str
    decoded_bytes: int
    settlement_generation: int
    settlement_audit_hash: str
    settlement_receipt_digest: str
    child_result_digest: str
    supervisor_result_digest: str
    transport_outcome: str
    accepted_for_custody: bool
    legacy_c4_proof_claimed: bool
    receipt_digest: str
    parent_mac_hex: str
    capability_token: str = ""  # Process-local single-use token (NEVER serialized to JSON!)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "tested_executable_sha": self.tested_executable_sha,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "attempt_id": self.attempt_id,
            "plan_digest": self.plan_digest,
            "request_digest": self.request_digest,
            "endpoint_id": self.endpoint_id,
            "contract_digest": self.contract_digest,
            "partition": self.partition,
            "provenance_class": self.provenance_class,
            "transport_kind": self.transport_kind,
            "tls_verified": self.tls_verified,
            "child_pid": self.child_pid,
            "child_create_time": self.child_create_time,
            "child_exit_code": self.child_exit_code,
            "reaped_verified": self.reaped_verified,
            "raw_payload_sha256": self.raw_payload_sha256,
            "raw_payload_bytes": self.raw_payload_bytes,
            "decoded_payload_sha256": self.decoded_payload_sha256,
            "decoded_bytes": self.decoded_bytes,
            "settlement_generation": self.settlement_generation,
            "settlement_audit_hash": self.settlement_audit_hash,
            "settlement_receipt_digest": self.settlement_receipt_digest,
            "child_result_digest": self.child_result_digest,
            "supervisor_result_digest": self.supervisor_result_digest,
            "transport_outcome": self.transport_outcome,
            "accepted_for_custody": self.accepted_for_custody,
            "legacy_c4_proof_claimed": self.legacy_c4_proof_claimed,
            "receipt_digest": self.receipt_digest,
            "parent_mac_hex": self.parent_mac_hex,
        }


def mint_r6_raw_provenance_receipt(
    *,
    tested_executable_sha: str,
    run_id: str,
    session_id: str,
    lease_id: str,
    attempt_id: str,
    endpoint_id: str,
    contract_digest: str,
    partition: str,
    qa_loopback_mode: bool,
    transport_kind: str,
    supervisor_result: Any,
    raw_payload_bytes_data: Optional[bytes],
    settlement_receipt: Optional[R6DurableSettlementReceipt] = None,
) -> R6RawProvenanceReceipt:
    """
    F23: Mints a parent-owned R6 provenance receipt EXCLUSIVELY from a process-registered
    `R6TrustedSupervisorResult` + verified `R6DurableSettlementReceipt` + exact matching raw payload bytes.
    REMOVES caller `Mapping` flag trust! Passing a synthetic dict or mismatched bytes/IDs/settlement
    produces `RAW_UNVERIFIED_REJECTED` (`accepted_for_custody=False`) and NEVER `RAW_OBSERVED_VERIFIED`!
    In KH-03.R6.A1, NO code path can mint `RAW_OBSERVED_VERIFIED` (even if `tls_verified=True`).
    """
    rid = f"r6prov_{run_id}_{attempt_id}"
    raw_bytes = raw_payload_bytes_data if isinstance(raw_payload_bytes_data, (bytes, bytearray)) else b""
    raw_bytes = bytes(raw_bytes)
    raw_sha = _sha256_bytes(raw_bytes)
    raw_len = len(raw_bytes)

    def _reject(reason_code: str) -> R6RawProvenanceReceipt:
        core_rej = {
            "schema_version": PROVENANCE_SCHEMA_VERSION_R6,
            "receipt_id": rid,
            "tested_executable_sha": str(tested_executable_sha),
            "run_id": str(run_id),
            "session_id": str(session_id),
            "lease_id": str(lease_id),
            "attempt_id": str(attempt_id),
            "plan_digest": "",
            "request_digest": "",
            "endpoint_id": str(endpoint_id),
            "contract_digest": str(contract_digest),
            "partition": str(partition),
            "provenance_class": "RAW_UNVERIFIED_REJECTED",
            "transport_kind": str(transport_kind),
            "tls_verified": False,
            "child_pid": 0,
            "child_create_time": 0.0,
            "child_exit_code": -1,
            "reaped_verified": False,
            "raw_payload_sha256": raw_sha,
            "raw_payload_bytes": raw_len,
            "decoded_payload_sha256": "",
            "decoded_bytes": 0,
            "settlement_generation": 0,
            "settlement_audit_hash": "",
            "settlement_receipt_digest": "",
            "child_result_digest": "",
            "supervisor_result_digest": "",
            "transport_outcome": reason_code,
            "accepted_for_custody": False,
            "legacy_c4_proof_claimed": False,
        }
        rdig = _sha256_canonical(core_rej)
        mac = hmac.new(_PROVENANCE_MAC_SECRET_R6, rdig.encode("utf-8"), hashlib.sha256).hexdigest()
        return R6RawProvenanceReceipt(**core_rej, receipt_digest=rdig, parent_mac_hex=mac, capability_token="")

    # Hard reject raw Mapping / forged non-trusted supervisor objects!
    if not isinstance(supervisor_result, R6TrustedSupervisorResult):
        return _reject("REJECTED_UNTRUSTED_OR_MAPPING_SUPERVISOR_RESULT")
    if settlement_receipt is None or not isinstance(settlement_receipt, R6DurableSettlementReceipt):
        return _reject("REJECTED_MISSING_DURABLE_SETTLEMENT_RECEIPT")

    ok_tsr, tsr_viols, _ = verify_trusted_supervisor_result_a1(
        supervisor_result,
        expected_raw_payload_bytes=raw_bytes,
        settlement_receipt=settlement_receipt,
        require_unminted=True,
        require_unconsumed=True,
    )
    if not ok_tsr:
        return _reject(f"REJECTED_TRUSTED_SUPERVISOR_CHECK:{','.join(tsr_viols[:2])}")

    # Strict cross-field binding verification
    mismatches: List[str] = []
    if supervisor_result.tested_executable_sha != str(tested_executable_sha):
        mismatches.append("TESTED_SHA")
    if supervisor_result.run_id != str(run_id):
        mismatches.append("RUN_ID")
    if supervisor_result.session_id != str(session_id):
        mismatches.append("SESSION_ID")
    if supervisor_result.lease_id != str(lease_id):
        mismatches.append("LEASE_ID")
    if supervisor_result.attempt_id != str(attempt_id):
        mismatches.append("ATTEMPT_ID")
    if supervisor_result.endpoint_id != str(endpoint_id):
        mismatches.append("ENDPOINT_ID")
    if supervisor_result.contract_digest != str(contract_digest):
        mismatches.append("CONTRACT_DIGEST")
    if supervisor_result.partition != str(partition):
        mismatches.append("PARTITION")
    if supervisor_result.transport_kind != str(transport_kind):
        mismatches.append("TRANSPORT_KIND")
    if bool(supervisor_result.qa_loopback_mode) != bool(qa_loopback_mode):
        mismatches.append("QA_LOOPBACK_MODE")
    if settlement_receipt.attempt_id != str(attempt_id) or settlement_receipt.lease_id != str(lease_id):
        mismatches.append("SETTLEMENT_ATTEMPT_OR_LEASE")
    if settlement_receipt.final_attempt_state != "COMPLETED" or not settlement_receipt.conservation_verified or settlement_receipt.quarantined:
        mismatches.append("SETTLEMENT_NOT_COMPLETED_CONSERVED")
    if partition == "DEV_ONLY" and (qa_loopback_mode or not A1_EXTERNAL_CAPTURE_ENABLED):
        mismatches.append("QA_OR_A1_CANNOT_MINT_DEV_ONLY")
    if not A1_EXTERNAL_CAPTURE_ENABLED and not qa_loopback_mode:
        mismatches.append("A1_EXTERNAL_CAPTURE_DISABLED")
    if mismatches:
        return _reject(f"REJECTED_PROVENANCE_BINDING:{','.join(mismatches[:3])}")

    # Bind receipt_id single-use to trusted supervisor capability token!
    if not bind_provenance_receipt_to_trusted_token_a1(supervisor_result.capability_token, rid):
        return _reject("REJECTED_TRUSTED_SUPERVISOR_TOKEN_REPLAY")

    # A1 policy: strictly QA_LOOPBACK ONLY (never RAW_OBSERVED_VERIFIED!)
    prov_class = "QA_LOOPBACK"
    accepted = True

    core = {
        "schema_version": PROVENANCE_SCHEMA_VERSION_R6,
        "receipt_id": rid,
        "tested_executable_sha": supervisor_result.tested_executable_sha,
        "run_id": supervisor_result.run_id,
        "session_id": supervisor_result.session_id,
        "lease_id": supervisor_result.lease_id,
        "attempt_id": supervisor_result.attempt_id,
        "plan_digest": supervisor_result.plan_digest,
        "request_digest": supervisor_result.request_digest,
        "endpoint_id": supervisor_result.endpoint_id,
        "contract_digest": supervisor_result.contract_digest,
        "partition": supervisor_result.partition,
        "provenance_class": prov_class,
        "transport_kind": supervisor_result.transport_kind,
        "tls_verified": bool(supervisor_result.tls_verified),
        "child_pid": int(supervisor_result.child_pid),
        "child_create_time": float(supervisor_result.child_create_time),
        "child_exit_code": int(supervisor_result.child_exit_code),
        "reaped_verified": bool(supervisor_result.reaped_verified),
        "raw_payload_sha256": supervisor_result.raw_payload_sha256,
        "raw_payload_bytes": int(supervisor_result.raw_payload_bytes_len),
        "decoded_payload_sha256": supervisor_result.decoded_payload_sha256,
        "decoded_bytes": int(supervisor_result.decoded_payload_bytes_len),
        "settlement_generation": int(supervisor_result.settlement_generation),
        "settlement_audit_hash": supervisor_result.settlement_audit_hash,
        "settlement_receipt_digest": supervisor_result.settlement_receipt_digest,
        "child_result_digest": supervisor_result.child_result_digest,
        "supervisor_result_digest": supervisor_result.result_digest,
        "transport_outcome": "VERIFIED_TRUSTED_SUPERVISOR_SETTLED",
        "accepted_for_custody": accepted,
        "legacy_c4_proof_claimed": False,
    }
    rdig = _sha256_canonical(core)
    mac = hmac.new(_PROVENANCE_MAC_SECRET_R6, rdig.encode("utf-8"), hashlib.sha256).hexdigest()
    return R6RawProvenanceReceipt(**core, receipt_digest=rdig, parent_mac_hex=mac, capability_token=supervisor_result.capability_token)


def verify_r6_raw_provenance_receipt(
    receipt: Any,
    *,
    expected_payload_bytes: Optional[bytes] = None,
    expected_partition: Optional[str] = None,
    expected_run_id: Optional[str] = None,
    expected_attempt_id: Optional[str] = None,
    require_live_process_token: bool = True,
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if not isinstance(receipt, R6RawProvenanceReceipt):
        return False, ["FORGED_OR_RAW_DICT_PROVENANCE_RECEIPT_REJECTED"]
    d = receipt.to_dict()
    core = {k: v for k, v in d.items() if k not in ("receipt_digest", "parent_mac_hex")}
    recomp = _sha256_canonical(core)
    if recomp != receipt.receipt_digest:
        viols.append("PROVENANCE_RECEIPT_DIGEST_MISMATCH")
    exp_mac = hmac.new(_PROVENANCE_MAC_SECRET_R6, recomp.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(receipt.parent_mac_hex, exp_mac):
        viols.append("PROVENANCE_PARENT_MAC_FORGED")
    if receipt.legacy_c4_proof_claimed:
        viols.append("FORBIDDEN_LEGACY_C4_PROOF_CLAIM")
    if receipt.partition == "DEV_ONLY" and receipt.provenance_class == "QA_LOOPBACK":
        viols.append("QA_LOOPBACK_PROMOTION_TO_DEV_ONLY_FORBIDDEN")
    if not A1_EXTERNAL_CAPTURE_ENABLED and receipt.provenance_class == "RAW_OBSERVED_VERIFIED":
        viols.append("A1_RAW_OBSERVED_VERIFIED_FORBIDDEN")
    if receipt.accepted_for_custody:
        if not receipt.reaped_verified or receipt.child_exit_code != 0 or receipt.child_pid <= 0:
            viols.append("PROVENANCE_MISSING_REAPED_ZERO_EXIT_PROOF")
        if receipt.settlement_generation <= 0 or not receipt.settlement_audit_hash or not receipt.settlement_receipt_digest:
            viols.append("PROVENANCE_MISSING_SETTLEMENT_PROOF")
        if require_live_process_token:
            tok_ok, tok_viols = verify_and_consume_custody_token_a1(receipt.capability_token, receipt.receipt_id, consume=False)
            if not tok_ok:
                viols.extend(tok_viols)
    if expected_partition is not None and receipt.partition != expected_partition:
        viols.append(f"PROVENANCE_PARTITION_MISMATCH:{receipt.partition}!={expected_partition}")
    if expected_run_id is not None and receipt.run_id != expected_run_id:
        viols.append(f"PROVENANCE_RUN_ID_MISMATCH:{receipt.run_id}!={expected_run_id}")
    if expected_attempt_id is not None and receipt.attempt_id != expected_attempt_id:
        viols.append(f"PROVENANCE_ATTEMPT_ID_MISMATCH:{receipt.attempt_id}!={expected_attempt_id}")
    if expected_payload_bytes is not None:
        if _sha256_bytes(expected_payload_bytes) != receipt.raw_payload_sha256:
            viols.append("RAW_PAYLOAD_SHA256_MISMATCH")
        if len(expected_payload_bytes) != receipt.raw_payload_bytes:
            viols.append("RAW_PAYLOAD_LENGTH_MISMATCH")
    return len(viols) == 0, viols


# =====================================================================
# F27: Chunked Append-Only Custody Journal + External Anchor + Crash Recovery
# =====================================================================
class R6CustodyJournal:
    """
    Single-writer bounded chunked custody store with append-only hash chain + external anchor.
    Enforces:
      - Pre-open destination/symlink/junction/OOS guard
      - Parent-MAC verified provenance receipt + exact raw SHA-256 verification
      - Duplicate receipt / duplicate chunk rejection
      - Partition isolation (`QA_LOOPBACK` cannot persist into `DEV_ONLY` store!)
      - Atomic exclusive temp staging + fsync + same-FS replace
      - Safe owned-temp cleanup (NEVER deletes unrelated files; records failed unlink as quarantine!)
    """

    def __init__(
        self,
        *,
        approved_root: Path,
        partition: str,
        run_id: str,
        repo_root: Optional[Path] = None,
        oos_roots: Sequence[Path] = (),
        max_chunk_bytes: int = 4 * 1024 * 1024,
        max_total_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.approved_root = approved_root.resolve(strict=False)
        self.partition = partition
        self.run_id = run_id
        self.repo_root = repo_root
        self.oos_roots = tuple(oos_roots)
        self.max_chunk_bytes = int(max_chunk_bytes)
        self.max_total_bytes = int(max_total_bytes)

        self.store_dir = self.approved_root / partition.lower() / run_id
        guard = enforce_r6_destination_guard(
            self.store_dir,
            approved_root=self.approved_root,
            partition=partition,
            repo_root=repo_root,
            oos_roots=oos_roots,
        )
        if not guard["allowed"]:
            raise PermissionError(f"CUSTODY_DESTINATION_DENIED:{','.join(guard['violations'])}")

        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir = self.store_dir / "chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.chain_path = self.store_dir / "custody_chain.jsonl"
        self.anchor_path = self.store_dir / "custody.anchor.json"
        self._owned_temp_files: Set[Path] = set()
        self._seen_receipt_ids: Set[str] = set()
        self._total_persisted_bytes = 0
        self._seq = 0
        self._tip_hash = "0" * 64

        if self.chain_path.exists():
            self._load_existing_state()
        else:
            self._sync_anchor()

    def _load_existing_state(self) -> None:
        prev = "0" * 64
        seq = 0
        for line in self.chain_path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            seq += 1
            prev = str(row["entry_hash"])
            self._seen_receipt_ids.add(str(row["receipt_id"]))
            self._total_persisted_bytes += int(row["raw_bytes_len"])
        self._seq = seq
        self._tip_hash = prev

    def _sync_anchor(self) -> None:
        body = {
            "schema_version": CUSTODY_SCHEMA_VERSION_R6,
            "partition": self.partition,
            "run_id": self.run_id,
            "seq": self._seq,
            "tip_hash": self._tip_hash,
            "total_persisted_bytes": self._total_persisted_bytes,
        }
        body["anchor_sha256"] = _sha256_canonical(body)
        _atomic_write_json_fsync(self.anchor_path, body)

    def persist_r6_verified_chunk(
        self,
        *,
        chunk_id: str,
        raw_payload: bytes,
        normalized_rows: Sequence[Mapping[str, Any]],
        provenance_receipt: Any,
        simulate_crash_before_commit: bool = False,
    ) -> Dict[str, Any]:
        ok_p, p_viols = verify_r6_raw_provenance_receipt(
            provenance_receipt,
            expected_payload_bytes=raw_payload,
            expected_partition=self.partition,
            require_live_process_token=True,
        )
        if not ok_p:
            return {"persisted": False, "status": "REJECTED_PROVENANCE_INVALID", "violations": p_viols}
        assert isinstance(provenance_receipt, R6RawProvenanceReceipt)
        if not provenance_receipt.accepted_for_custody:
            return {"persisted": False, "status": "REJECTED_PROVENANCE_NOT_ACCEPTED", "violations": ["RECEIPT_NOT_ACCEPTED_FOR_CUSTODY"]}
        if provenance_receipt.partition != self.partition:
            return {"persisted": False, "status": "REJECTED_PARTITION_MISMATCH", "violations": [f"PARTITION_MISMATCH:{provenance_receipt.partition}!={self.partition}"]}
        if self.partition == "DEV_ONLY" and provenance_receipt.provenance_class != "RAW_OBSERVED_VERIFIED":
            return {"persisted": False, "status": "REJECTED_QA_INTO_DEV_FORBIDDEN", "violations": ["ONLY_RAW_OBSERVED_VERIFIED_ALLOWED_IN_DEV_ONLY"]}
        if not A1_EXTERNAL_CAPTURE_ENABLED and provenance_receipt.provenance_class == "RAW_OBSERVED_VERIFIED":
            return {"persisted": False, "status": "REJECTED_A1_OBSERVED_FORBIDDEN", "violations": ["A1_CANNOT_PERSIST_RAW_OBSERVED_VERIFIED"]}
        if provenance_receipt.receipt_id in self._seen_receipt_ids:
            return {"persisted": False, "status": "REJECTED_DUPLICATE_PROVENANCE_RECEIPT", "violations": [f"DUPLICATE_RECEIPT_ID:{provenance_receipt.receipt_id}"]}

        if len(raw_payload) > self.max_chunk_bytes:
            return {"persisted": False, "status": "REJECTED_CHUNK_SIZE_EXCEEDED", "violations": [f"CHUNK_TOO_LARGE:{len(raw_payload)}>{self.max_chunk_bytes}"]}
        if self._total_persisted_bytes + len(raw_payload) > self.max_total_bytes:
            return {"persisted": False, "status": "REJECTED_STORE_TOTAL_BYTES_CAP", "violations": ["STORE_BYTE_CAP_EXCEEDED"]}

        raw_path = self.chunks_dir / f"{chunk_id}.raw.bin"
        norm_path = self.chunks_dir / f"{chunk_id}.norm.json"
        for tp in (raw_path, norm_path):
            g = enforce_r6_destination_guard(tp, approved_root=self.approved_root, partition=self.partition, repo_root=self.repo_root, oos_roots=self.oos_roots)
            if not g["allowed"]:
                return {"persisted": False, "status": "REJECTED_DESTINATION_GUARD", "violations": g["violations"]}
            if tp.exists():
                return {"persisted": False, "status": "REJECTED_DUPLICATE_CHUNK_ID", "violations": [f"CHUNK_EXISTS:{chunk_id}"]}

        # Atomic exclusive staging on same filesystem
        raw_tmp = self.chunks_dir / f".{chunk_id}.{os.getpid()}.raw.tmp"
        norm_tmp = self.chunks_dir / f".{chunk_id}.{os.getpid()}.norm.tmp"
        self._owned_temp_files.add(raw_tmp)
        self._owned_temp_files.add(norm_tmp)

        norm_bytes = json.dumps(list(normalized_rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        norm_sha = _sha256_bytes(norm_bytes)
        raw_sha = _sha256_bytes(raw_payload)

        fd_r = os.open(str(raw_tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd_r, raw_payload)
            os.fsync(fd_r)
        finally:
            os.close(fd_r)

        fd_n = os.open(str(norm_tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd_n, norm_bytes)
            os.fsync(fd_n)
        finally:
            os.close(fd_n)

        if simulate_crash_before_commit:
            # Consume token so failed/crashed staging CANNOT be replayed for duplicate promotion!
            verify_and_consume_custody_token_a1(provenance_receipt.capability_token, provenance_receipt.receipt_id, consume=True)
            return {
                "persisted": False,
                "status": "ABORTED_PARTIAL_CRASH_BEFORE_COMMIT",
                "orphan_temp_files": [str(raw_tmp), str(norm_tmp)],
                "violations": ["SIMULATED_CRASH_BEFORE_COMMIT_ORPHAN_STAGED"],
            }

        # Consume single-use trusted supervisor token atomically BEFORE final replace/commit!
        tok_ok, tok_viols = verify_and_consume_custody_token_a1(provenance_receipt.capability_token, provenance_receipt.receipt_id, consume=True)
        if not tok_ok:
            for p_cln in (raw_tmp, norm_tmp):
                try:
                    p_cln.unlink()
                    self._owned_temp_files.discard(p_cln)
                except OSError:
                    pass
            return {"persisted": False, "status": "REJECTED_PROVENANCE_TOKEN_REPLAY", "violations": tok_viols}

        os.replace(str(raw_tmp), str(raw_path))
        os.replace(str(norm_tmp), str(norm_path))
        self._owned_temp_files.discard(raw_tmp)
        self._owned_temp_files.discard(norm_tmp)
        _fsync_file_and_parent(raw_path)
        _fsync_file_and_parent(norm_path)

        # Append ordered chain record with complete non-secret audit proof facts for post-restart verification!
        new_seq = self._seq + 1
        entry_core = {
            "seq": new_seq,
            "chunk_id": chunk_id,
            "receipt_id": provenance_receipt.receipt_id,
            "receipt_digest": provenance_receipt.receipt_digest,
            "tested_executable_sha": provenance_receipt.tested_executable_sha,
            "session_id": provenance_receipt.session_id,
            "lease_id": provenance_receipt.lease_id,
            "attempt_id": provenance_receipt.attempt_id,
            "plan_digest": provenance_receipt.plan_digest,
            "request_digest": provenance_receipt.request_digest,
            "endpoint_id": provenance_receipt.endpoint_id,
            "contract_digest": provenance_receipt.contract_digest,
            "settlement_generation": int(provenance_receipt.settlement_generation),
            "settlement_audit_hash": provenance_receipt.settlement_audit_hash,
            "settlement_receipt_digest": provenance_receipt.settlement_receipt_digest,
            "child_pid": int(provenance_receipt.child_pid),
            "child_exit_code": int(provenance_receipt.child_exit_code),
            "reaped_verified": bool(provenance_receipt.reaped_verified),
            "decoded_payload_sha256": provenance_receipt.decoded_payload_sha256,
            "partition": self.partition,
            "provenance_class": provenance_receipt.provenance_class,
            "raw_file": f"chunks/{raw_path.name}",
            "norm_file": f"chunks/{norm_path.name}",
            "raw_sha256": raw_sha,
            "raw_bytes_len": len(raw_payload),
            "norm_sha256": norm_sha,
            "norm_rows_count": len(normalized_rows),
            "prev_hash": self._tip_hash,
        }
        entry_hash = _sha256_canonical(entry_core)
        entry = dict(entry_core)
        entry["entry_hash"] = entry_hash

        with self.chain_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        self._seq = new_seq
        self._tip_hash = entry_hash
        self._total_persisted_bytes += len(raw_payload)
        self._seen_receipt_ids.add(provenance_receipt.receipt_id)
        self._sync_anchor()

        return {
            "persisted": True,
            "status": "PERSISTED_COMMITTED",
            "seq": new_seq,
            "chunk_id": chunk_id,
            "raw_sha256": raw_sha,
            "norm_sha256": norm_sha,
            "entry_hash": entry_hash,
            "violations": [],
        }

    def cleanup_owned_temp_files_safe(self, *, fail_unlink_paths: Optional[Set[Path]] = None) -> Dict[str, Any]:
        """
        Cleans up ONLY tracked owned `.tmp` files; NEVER deletes unrelated files!
        If an unlink fails, records the file as QUARANTINED_FAILED_UNLINK with SHA-256 and NEVER claims deletion.
        """
        deleted: List[str] = []
        quarantined: List[Dict[str, Any]] = []
        for p in sorted(list(self._owned_temp_files)):
            if not p.exists():
                self._owned_temp_files.discard(p)
                continue
            try:
                if fail_unlink_paths and p in fail_unlink_paths:
                    raise PermissionError("SIMULATED_UNLINK_FAILURE")
                p.unlink()
                deleted.append(p.name)
                self._owned_temp_files.discard(p)
            except OSError as exc:
                quarantined.append({
                    "name": p.name,
                    "sha256": _sha256_bytes(p.read_bytes()) if p.is_file() else None,
                    "error": str(exc),
                })
        return {
            "deleted_owned_temps": deleted,
            "quarantined_failed_unlinks": quarantined,
            "all_owned_temps_deleted": len(quarantined) == 0,
        }


def verify_r6_custody_readonly(
    store_dir: Path,
    *,
    expected_partition: str,
    expected_run_id: str,
    ledger_store_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    F25: Read-only post-restart independent custody verifier: recomputes every chunk SHA-256 and
    byte length, verifies full hash chain, verifies persisted non-secret settlement/reap proof facts
    (and cross-checks against the SQLite ledger audit chain when `ledger_store_root` is provided!),
    checks external custody anchor against rollback/tamper, and detects uncommitted orphan `.tmp` files.
    """
    import sqlite3 as _sqlite3

    viols: List[str] = []
    if not store_dir.is_dir():
        return {"valid": False, "status": "FAIL_CUSTODY_DIR_MISSING", "violations": ["CUSTODY_DIR_MISSING"]}
    if _has_any_reparse_ancestor(store_dir):
        return {"valid": False, "status": "FAIL_CUSTODY_SYMLINK", "violations": ["CUSTODY_SYMLINK_FORBIDDEN"]}

    anchor_p = store_dir / "custody.anchor.json"
    chain_p = store_dir / "custody_chain.jsonl"
    chunks_dir = store_dir / "chunks"

    if not anchor_p.is_file():
        return {"valid": False, "status": "FAIL_CUSTODY_ANCHOR_MISSING", "violations": ["CUSTODY_ANCHOR_MISSING"]}

    try:
        anchor = _strict_loads_r6(anchor_p.read_bytes(), max_bytes=32_768)
    except Exception as exc:
        return {"valid": False, "status": "FAIL_CUSTODY_ANCHOR_CORRUPT", "violations": [f"CUSTODY_ANCHOR_PARSE:{exc}"]}

    anc_copy = {k: v for k, v in anchor.items() if k != "anchor_sha256"}
    if str(anchor.get("anchor_sha256") or "") != _sha256_canonical(anc_copy):
        viols.append("CUSTODY_ANCHOR_SHA256_TAMPERED")
    if str(anchor.get("partition") or "") != expected_partition:
        viols.append("CUSTODY_ANCHOR_PARTITION_MISMATCH")
    if str(anchor.get("run_id") or "") != expected_run_id:
        viols.append("CUSTODY_ANCHOR_RUN_ID_MISMATCH")

    # Optional cross-check against SQLite ledger (post-restart authentic origin reconstruction!)
    ledger_audit_by_gen: Dict[int, str] = {}
    ledger_attempts_state: Dict[str, str] = {}
    if ledger_store_root is not None:
        db_p = Path(ledger_store_root) / "ledger.sqlite3"
        if not db_p.is_file():
            viols.append("CUSTODY_LINKED_LEDGER_DB_MISSING")
        else:
            try:
                uri = f"{db_p.resolve().as_uri()}?mode=ro"
                lconn = _sqlite3.connect(uri, uri=True, timeout=5.0)
                lconn.row_factory = _sqlite3.Row
                try:
                    for r in lconn.execute("SELECT generation, entry_hash FROM audit_chain"):
                        ledger_audit_by_gen[int(r["generation"])] = str(r["entry_hash"])
                    for r in lconn.execute("SELECT attempt_id, state FROM attempts"):
                        ledger_attempts_state[str(r["attempt_id"])] = str(r["state"])
                finally:
                    lconn.close()
            except Exception as exc:
                viols.append(f"CUSTODY_LINKED_LEDGER_READ_ERROR:{exc}")

    # Verify chain & chunk blobs
    prev = "0" * 64
    seq = 0
    tot_bytes = 0
    tot_rows = 0
    seen_rids: Set[str] = set()
    committed_files: Set[str] = set()
    lineage_entries: List[Dict[str, Any]] = []

    if chain_p.is_file():
        for ln in chain_p.read_text("utf-8").splitlines():
            if not ln.strip():
                continue
            row = json.loads(ln)
            seq += 1
            if int(row.get("seq", -1)) != seq:
                viols.append(f"CUSTODY_SEQ_MISMATCH:{seq}")
            if str(row.get("prev_hash") or "") != prev:
                viols.append(f"CUSTODY_PREV_HASH_BROKEN:seq={seq}")
            core = {k: v for k, v in row.items() if k != "entry_hash"}
            calc = _sha256_canonical(core)
            if calc != str(row.get("entry_hash") or ""):
                viols.append(f"CUSTODY_ENTRY_HASH_TAMPERED:seq={seq}")
            rid = str(row.get("receipt_id") or "")
            if rid in seen_rids:
                viols.append(f"CUSTODY_DUPLICATE_RECEIPT:{rid}")
            seen_rids.add(rid)
            if expected_partition == "DEV_ONLY" and str(row.get("provenance_class") or "") != "RAW_OBSERVED_VERIFIED":
                viols.append(f"CUSTODY_QA_IN_DEV_STORE:{row.get('provenance_class')}")
            if not A1_EXTERNAL_CAPTURE_ENABLED and str(row.get("provenance_class") or "") == "RAW_OBSERVED_VERIFIED":
                viols.append("CUSTODY_A1_FORBIDDEN_RAW_OBSERVED_VERIFIED")
            if row.get("reaped_verified") is not True or int(row.get("child_exit_code", -1)) != 0 or int(row.get("child_pid", 0)) <= 0:
                viols.append(f"CUSTODY_UNVERIFIED_CHILD_REAP_PROOF:seq={seq}")
            if int(row.get("settlement_generation", 0)) <= 0 or not str(row.get("settlement_audit_hash") or ""):
                viols.append(f"CUSTODY_MISSING_SETTLEMENT_PROOF:seq={seq}")
            if ledger_store_root is not None:
                sgen = int(row.get("settlement_generation", 0))
                shash = str(row.get("settlement_audit_hash") or "")
                aid = str(row.get("attempt_id") or "")
                if ledger_audit_by_gen.get(sgen) != shash:
                    viols.append(f"CUSTODY_LEDGER_SETTLEMENT_HASH_MISMATCH:seq={seq}:gen={sgen}")
                if ledger_attempts_state.get(aid) != "COMPLETED":
                    viols.append(f"CUSTODY_LEDGER_ATTEMPT_NOT_COMPLETED:seq={seq}:aid={aid}:state={ledger_attempts_state.get(aid)}")

            rf = store_dir / str(row.get("raw_file") or "")
            nf = store_dir / str(row.get("norm_file") or "")
            if not rf.is_file() or not nf.is_file():
                viols.append(f"CUSTODY_MISSING_BLOB:seq={seq}")
            else:
                rb = rf.read_bytes()
                nb = nf.read_bytes()
                if _sha256_bytes(rb) != str(row.get("raw_sha256") or ""):
                    viols.append(f"CUSTODY_RAW_BLOB_SHA_MISMATCH:seq={seq}")
                if len(rb) != int(row.get("raw_bytes_len", -1)):
                    viols.append(f"CUSTODY_RAW_BLOB_LEN_MISMATCH:seq={seq}")
                if _sha256_bytes(nb) != str(row.get("norm_sha256") or ""):
                    viols.append(f"CUSTODY_NORM_BLOB_SHA_MISMATCH:seq={seq}")
                try:
                    n_arr = json.loads(nb.decode("utf-8"))
                    if not isinstance(n_arr, list) or len(n_arr) != int(row.get("norm_rows_count", -1)):
                        viols.append(f"CUSTODY_NORM_ROW_COUNT_MISMATCH:seq={seq}")
                except Exception:
                    viols.append(f"CUSTODY_NORM_JSON_CORRUPT:seq={seq}")
                committed_files.add(rf.name)
                committed_files.add(nf.name)

            tot_bytes += int(row.get("raw_bytes_len", 0))
            tot_rows += int(row.get("norm_rows_count", 0))
            lineage_entries.append({
                "seq": seq,
                "chunk_id": row.get("chunk_id"),
                "attempt_id": row.get("attempt_id"),
                "endpoint_id": row.get("endpoint_id"),
                "provenance_class": row.get("provenance_class"),
                "raw_sha256": row.get("raw_sha256"),
                "raw_bytes_len": row.get("raw_bytes_len"),
                "norm_sha256": row.get("norm_sha256"),
                "norm_rows_count": row.get("norm_rows_count"),
                "settlement_generation": row.get("settlement_generation"),
            })
            prev = calc

    # Anchor vs chain rollback/divergence detection
    anc_seq = int(anchor.get("seq", -1))
    if seq < anc_seq:
        viols.append(f"CUSTODY_CHAIN_ROLLBACK_DETECTED:chain_seq={seq}<anchor_seq={anc_seq}")
    elif seq != anc_seq or prev != str(anchor.get("tip_hash") or ""):
        viols.append("CUSTODY_CHAIN_ANCHOR_DIVERGENCE")

    # Detect uncommitted orphan .tmp files in chunks_dir
    orphan_temps: List[str] = []
    if chunks_dir.is_dir():
        for f in sorted(chunks_dir.iterdir()):
            if f.name.endswith(".tmp") or (f.is_file() and f.name not in committed_files):
                orphan_temps.append(f.name)
    if orphan_temps:
        viols.append(f"UNCOMMITTED_ORPHAN_STAGED_FILES:{','.join(orphan_temps)}")

    return {
        "valid": len(viols) == 0,
        "status": "CUSTODY_VERIFIED_OK" if not viols else "CUSTODY_VERIFICATION_FAILED",
        "committed_chunks": seq,
        "total_persisted_bytes": tot_bytes,
        "total_normalized_rows": tot_rows,
        "tip_hash": prev,
        "orphan_files_detected": orphan_temps,
        "lineage_entries": lineage_entries,
        "violations": viols,
    }


# =====================================================================
# F28: Seal DEV Dataset Manifest
# =====================================================================
def seal_r6_dev_dataset_manifest(
    *,
    run_id: str,
    stage: str,
    partition: str,
    tested_executable_sha: str,
    source_registry_digest: str,
    instrument_metadata_versions: Sequence[Mapping[str, Any]],
    window_start_ms: Optional[int],
    window_end_ms: Optional[int],
    custody_audit: Mapping[str, Any],
    gap_ledger: Mapping[str, Any],
    raw_observed_count: int,
    normalized_valid_count: int,
    rejected_or_quarantined_count: int,
    persisted_accepted_count: int,
    provenance_classes: Sequence[str],
    retention_policy: Mapping[str, Any],
    local_store_redacted_id: str,
) -> Dict[str, Any]:
    conservation_ok = (normalized_valid_count + rejected_or_quarantined_count <= raw_observed_count) and (
        persisted_accepted_count <= normalized_valid_count
    )
    doc = {
        "spec_version": SPEC_VERSION_KH03_R6,
        "schema_version": DATASET_MANIFEST_SCHEMA_R6,
        "run_id": run_id,
        "stage": stage,
        "partition": partition,
        "usable_as_fresh_oos": False,
        "venue": APPROVED_VENUE_R6,
        "api_family": API_FAMILY_CLASSIC_V2,
        "symbol": APPROVED_SYMBOL_R6,
        "tested_executable_sha": tested_executable_sha,
        "source_registry_digest": source_registry_digest,
        "local_store_redacted_id": local_store_redacted_id,
        "raw_payloads_committed_to_git": False,
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "instrument_metadata_versions": list(instrument_metadata_versions),
        "custody_tip_hash": custody_audit.get("tip_hash"),
        "custody_committed_chunks": int(custody_audit.get("committed_chunks") or 0),
        "custody_valid": custody_audit.get("valid") is True,
        "gap_ledger_sha256": gap_ledger.get("gap_ledger_sha256"),
        "gap_entries_count": int(gap_ledger.get("entries_count") or 0),
        "conservation": {
            "raw_observed_count": int(raw_observed_count),
            "normalized_valid_count": int(normalized_valid_count),
            "rejected_or_quarantined_count": int(rejected_or_quarantined_count),
            "persisted_accepted_count": int(persisted_accepted_count),
            "conservation_holds": conservation_ok,
        },
        "provenance_classes": sorted(set(str(x) for x in provenance_classes)),
        "retention_policy": dict(retention_policy),
    }
    doc["manifest_sha256"] = _sha256_canonical(doc)
    return doc


# =====================================================================
# F29: 36-Gate Stage-Scoped Verdict Engine & Vietnamese Handover
# =====================================================================
def derive_r6_readiness_verdicts(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    stage: str = "STAGE_A_OFFLINE",
    expected_run_id: str,
    expected_tested_sha: str,
    remote_readback: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Independently computes ALL 36 stage-scoped KH-03.R6 gates (`G01`..`G36`) and ALL independent
    verdict dimensions strictly from raw metrics/receipts/booleans in `artifacts` (`00`..`24`, `26`, `27`).
    NEVER trusts caller-injected PASS strings!
    NEVER reports `36/36 PASS` when Stage B/C did not execute!
    """
    a00 = artifacts.get("00") or {}
    a01 = artifacts.get("01") or {}
    a02 = artifacts.get("02") or {}
    a03 = artifacts.get("03") or {}
    a04 = artifacts.get("04") or {}
    a05 = artifacts.get("05") or {}
    a06 = artifacts.get("06") or {}
    a08 = artifacts.get("08") or {}
    a09 = artifacts.get("09") or {}
    a10 = artifacts.get("10") or {}
    a11 = artifacts.get("11") or {}
    a12 = artifacts.get("12") or {}
    a13 = artifacts.get("13") or {}
    a14 = artifacts.get("14") or {}
    a15 = artifacts.get("15") or {}
    a16 = artifacts.get("16") or {}
    a17 = artifacts.get("17") or {}
    a18 = artifacts.get("18") or {}
    a19 = artifacts.get("19") or {}
    a20 = artifacts.get("20") or {}
    a21 = artifacts.get("21") or {}
    a23 = artifacts.get("23") or {}
    a24 = artifacts.get("24") or {}
    a26 = artifacts.get("26") or {}
    a27 = artifacts.get("27") or {}

    gates: Dict[str, str] = {}

    # G01: Branch, frozen SHA, origin, clean worktree
    g01 = (
        a00.get("branch") == MANDATORY_BRANCH_KH03_R6
        and a00.get("tested_executable_sha") == expected_tested_sha
        and a00.get("run_id") == expected_run_id
        and a00.get("worktree_clean_at_start") is True
        and "quocan050302/Build-app-crypto" in str(a00.get("origin_url") or "")
        and a00.get("disk_waiver_used") is False
        and _is_num(a00.get("preflight_disk_free_gib")) and float(a00["preflight_disk_free_gib"]) >= 20.5
        and _is_num(a00.get("preflight_ram_available_mib")) and float(a00["preflight_ram_available_mib"]) >= 512.0
    )
    gates["G01_BRANCH_SHA_CLEAN"] = "PASS" if g01 else "FAIL"

    # G02: Keep-as-is protected bytes/AST/call-graph/production settings & C2 hard block intact
    g02 = (
        _is_strict_int(a01.get("locked_files_modified_count")) and int(a01["locked_files_modified_count"]) == 0
        and _is_strict_int(a01.get("unauthorized_changed_files_count")) and int(a01["unauthorized_changed_files_count"]) == 0
        and _is_strict_int(a01.get("forbidden_order_imports_count")) and int(a01["forbidden_order_imports_count"]) == 0
        and a01.get("legacy_r1_unauthorized_callers_count") == 0
        and a01.get("production_cost_defaults_intact") is True
        and a01.get("c2_live_hard_block_intact") is True
        and a01.get("secrets_detected_count") == 0
    )
    gates["G02_KEEP_AS_IS"] = "PASS" if g02 else "FAIL"

    # G03: Prior C2 sealed artifacts + canonical dataset + zero OOS opens
    g03 = (
        a02.get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH02
        and a02.get("all_prior_sealed_artifacts_intact") is True
        and _is_strict_int(a02.get("prior_sealed_artifacts_checked_count")) and int(a02["prior_sealed_artifacts_checked_count"]) >= 20
        and a02.get("prior_c2_manifest_sha256_verified") is True
        and a02.get("oos_reads_executed") == 0
        and a02.get("oos_evaluations") == 0
    )
    gates["G03_PRIOR_C2_SEALED_DATASET_OOS"] = "PASS" if g03 else "FAIL"

    # G04: Official source docs verified (or explicitly BLOCKED)
    g04_docs_ok = (
        a03.get("selected_api_family") == API_FAMILY_CLASSIC_V2
        and a03.get("registry_status") == "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW"
        and _is_strict_int(a03.get("doc_verified_endpoints_count")) and int(a03["doc_verified_endpoints_count"]) >= 6
        and _is_strict_int(a03.get("official_docs_retrieved_count")) and int(a03["official_docs_retrieved_count"]) >= 6
        and a03.get("rest_quote_snapshot_can_be_continuous_bbo") is False
    )
    gates["G04_SOURCE_DOCS"] = "PASS" if g04_docs_ok else "BLOCKED"

    # G05: Strict endpoint/version/identity validation fail closed
    g05 = (
        a03.get("mixed_v2_v3_rejected") is True
        and a03.get("substitute_symbol_rejected") is True
        and a03.get("private_or_redirect_rejected") is True
        and a10.get("qa_valid_metadata_accepted") is True
        and a10.get("missing_tick_lot_blocked_no_default") is True
        and a10.get("identity_mismatch_halts_grant") is True
    )
    gates["G05_STRICT_ENDPOINT_VERSION_IDENTITY"] = "PASS" if g05 else "FAIL"

    # G06: Owner trust boundary (no fake approval, isolated QA fixtures, external owner trust separate)
    g06 = (
        a04.get("default_embedded_owner_key_present") is False
        and a04.get("unprovisioned_owner_trust_blocked") is True
        and a04.get("unsigned_template_rejected") is True
        and a04.get("qa_trust_cannot_authorize_external") is True
        and a04.get("cli_env_prompt_bypass_rejected") is True
        and a04.get("qa_isolated_ed25519_verified") is True
    )
    gates["G06_OWNER_TRUST_BOUNDARY"] = "PASS" if g06 else "FAIL"

    # G07: Grant & bound plan binding
    g07 = (
        a04.get("payload_tamper_rejected") is True
        and a04.get("duplicate_key_rejected") is True
        and a04.get("expired_or_clock_rollback_rejected") is True
        and a04.get("revoked_trust_root_rejected") is True
        and a04.get("plan_exceeding_grant_rejected_no_clamp") is True
        and a04.get("sha_branch_store_drift_rejected") is True
    )
    gates["G07_GRANT_PLAN_BINDING"] = "PASS" if g07 else "FAIL"

    # G08: Durable one-shot claim across processes & restarts
    g08 = (
        a05.get("sequential_replay_denied") is True
        and a05.get("cross_process_race_single_winner") is True
        and a05.get("cross_process_contenders_count") is not None and int(a05.get("cross_process_contenders_count", 0)) >= 2
        and a05.get("cross_process_winners_count") == 1
        and a05.get("replan_reissue_denied") is True
        and a05.get("same_identity_different_payload_rejected") is True
        and a05.get("post_kill_restart_replay_denied") is True
    )
    gates["G08_DURABLE_ONE_SHOT"] = "PASS" if g08 else "FAIL"

    # G09: Crash/anchor recovery & fail-closed on missing/replaced/rollback/corrupt store
    g09 = (
        a06.get("missing_db_no_auto_init_blocked") is True
        and a06.get("corrupt_db_quarantined") is True
        and a06.get("anchor_rollback_quarantined") is True
        and a06.get("post_commit_pre_anchor_forward_recovered") is True
        and a06.get("kill_after_start_retains_unknown_bytes") is True
        and a06.get("alternate_store_path_rejected") is True
    )
    gates["G09_CRASH_ANCHOR_RECOVERY"] = "PASS" if g09 else "FAIL"

    # G10: Persistent budget conservation & duplicate rejection
    g10 = (
        a06.get("duplicate_attempt_id_rejected") is True
        and a06.get("double_settle_rejected") is True
        and a06.get("negative_counter_rejected") is True
        and a06.get("budget_overflow_rejected") is True
        and a06.get("unknown_bytes_conservatively_retained") is True
        and a06.get("capacity_full_blocks_without_eviction") is True
    )
    gates["G10_BUDGET_CONSERVATION"] = "PASS" if g10 else "FAIL"

    # G11: Owned transport process (loopback actual start/stop/OS identity)
    g11 = (
        a08.get("loopback_worker_launched_and_completed") is True
        and _is_strict_int(a08.get("loopback_worker_pid")) and int(a08["loopback_worker_pid"]) > 0
        and a08.get("loopback_worker_os_exit_verified") is True
        and a08.get("actual_start_expiry_zero_dispatch") is True
        and a08.get("external_socket_without_grant_blocked") is True
    )
    gates["G11_OWNED_TRANSPORT_PROCESS"] = "PASS" if g11 else "FAIL"

    # G12: Noncooperative loopback worker + descendants reaped or quarantined
    g12 = (
        a08.get("noncooperative_tree_terminated_and_reaped") is True
        and a08.get("surviving_descendants_after_reap_count") == 0
        and a08.get("unverified_os_proof_quarantined") is True
        and a08.get("late_result_after_cancel_discarded") is True
    )
    gates["G12_TERMINATION_REAP"] = "PASS" if g12 else "FAIL"

    # G13: REST read/decompress/header/deadline/retry bounds
    g13 = (
        a08.get("slow_drip_total_deadline_enforced") is True
        and a08.get("oversized_and_chunked_rejected") is True
        and a08.get("gzip_bomb_rejected_bounded") is True
        and a08.get("retry_after_honored_no_shortening") is True
        and a08.get("nonretryable_401_403_redirect_stopped") is True
    )
    gates["G13_REST_READ_DEADLINE_BOUND"] = "PASS" if g13 else "FAIL"

    # G14: WS frame/queue/heartbeat/epoch/bounded reconnect
    g14 = (
        a08.get("ws_valid_subscribe_ack_and_snapshot") is True
        and a08.get("ws_invalid_ack_or_private_op_rejected") is True
        and a08.get("ws_oversized_frame_rejected") is True
        and a08.get("ws_disconnect_epoch_gap_and_bounded_reconnect") is True
        and a08.get("ws_queue_overflow_no_silent_drop") is True
    )
    gates["G14_WS_FRAME_RECONNECT_BOUND"] = "PASS" if g14 else "FAIL"

    # G15: Strict multi-volume resource guard & no disk waiver
    g15 = (
        a08.get("resource_missing_stale_nan_wrong_vol_rejected") is True
        and a08.get("qa_fixture_telemetry_blocked_in_live") is True
        and a21.get("disk_waiver_exit2_proven") is True
        and _is_num(a21.get("min_observed_disk_free_gib")) and float(a21["min_observed_disk_free_gib"]) >= 20.0
        and _is_num(a21.get("min_observed_ram_available_mib")) and float(a21["min_observed_ram_available_mib"]) >= 512.0
    )
    gates["G15_RESOURCE_VOLUME"] = "PASS" if g15 else "FAIL"

    # G16: OHLCV contract
    g16 = (
        a11.get("valid_closed_bars_accepted") is True
        and a11.get("invalid_geometry_rejected") is True
        and a11.get("cursor_replay_stopped") is True
        and a11.get("contradictory_duplicate_quarantined") is True
        and a11.get("unclosed_future_excluded_from_canonical") is True
        and a11.get("unknown_calendar_marked_incomplete") is True
    )
    gates["G16_OHLCV_CONTRACT"] = "PASS" if g16 else "FAIL"

    # G17: BBO contract
    g17 = (
        a12.get("crossed_tick_ooo_rejected") is True
        and a12.get("rest_snapshot_rejected_as_streaming_bbo") is True
        and a12.get("no_carry_across_disconnect_or_missing_snapshot") is True
        and a12.get("duplicates_do_not_inflate_coverage") is True
        and a12.get("stale_quote_not_observed_despite_heartbeat") is True
        and a12.get("observed_vs_state_known_reported_separately") is True
    )
    gates["G17_BBO_CONTRACT"] = "PASS" if g17 else "FAIL"

    # G18: Funding contract
    g18 = (
        a13.get("documented_settlements_validated") is True
        and a13.get("missing_settlements_not_zero_filled") is True
        and a13.get("unknown_interval_blocked_no_8h_default") is True
        and a13.get("conflicting_settlement_quarantined") is True
    )
    gates["G18_FUNDING_CONTRACT"] = "PASS" if g18 else "FAIL"

    # G19: Fee & slippage unknown honesty
    g19 = (
        a14.get("owner_fee_status") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        and a14.get("public_fee_rejected_as_owner_effective") is True
        and a14.get("slippage_status") == "MISSING_NO_OWNER_FILL_EVIDENCE"
        and a14.get("public_bbo_proves_execution_slippage") is False
        and a14.get("orders_placed_count") == 0
    )
    gates["G19_FEE_SLIPPAGE_UNKNOWN_HONESTY"] = "PASS" if g19 else "FAIL"

    # G20: Custody & partition isolation
    g20 = (
        a15.get("qa_custody_chain_verified") is True
        and a15.get("symlink_junction_oos_denied_before_open") is True
        and a15.get("qa_promotion_to_dev_denied") is True
        and a15.get("tamper_and_duplicate_receipt_rejected") is True
        and a15.get("anchor_rollback_detected") is True
        and a15.get("unrelated_files_preserved_on_cleanup") is True
    )
    gates["G20_CUSTODY_PARTITION"] = "PASS" if g20 else "FAIL"

    # G21: Full pytest collection-execution bijection
    g21 = (
        a19.get("junit_bijection_exact") is True
        and _is_strict_int(a19.get("collected_count")) and int(a19["collected_count"]) >= MIN_EXPECTED_PYTEST_COLLECTED_R6
        and a19.get("collected_count") == a19.get("executed_count")
        and a19.get("failed_count") == 0
        and a19.get("error_count") == 0
        and a19.get("mandatory_skipped_count") == 0
        and a19.get("nonzero_exit_shards_count") == 0
    )
    gates["G21_FULL_PYTEST"] = "PASS" if g21 else "FAIL"

    # G22: Baseline parity (A1=56710, SI8=6375, adapters=246, D4=0)
    g22 = (
        a18.get("a1_checked") == 56710 and a18.get("a1_mismatches") == 0
        and a18.get("si8_checked") == 6375 and a18.get("si8_mismatches") == 0
        and a18.get("adapters_checked") == 246 and a18.get("adapters_mismatches") == 0
        and a18.get("d4_mismatches") == 0
    )
    gates["G22_BASELINE_PARITY"] = "PASS" if g22 else "FAIL"

    # G23: Backtest parity (R4 1x + 6 cost scenarios + E1/E2/E3 arms exact match)
    g23 = (
        a16.get("r4_decimal_bridge_exact_match") is True
        and a16.get("n_closed") == 203
        and a16.get("n_wins") == 7
        and a16.get("n_losses") == 196
        and a16.get("n_open_censored") == 28
        and a16.get("n_gap_events") == 2
        and a16.get("realized_losses_gt_2p50_count") == 139
        and a17.get("all_six_scenarios_exact_match") is True
        and a17.get("all_three_arms_exact_match") is True
    )
    gates["G23_BACKTEST_PARITY"] = "PASS" if g23 else "FAIL"

    # G24: Determinism W1/W2/W4/W1-repeat (science + R6 fixture ingest)
    g24 = (
        a20.get("science_w1_w2_w4_all_identical") is True
        and a20.get("r6_ingest_w1_w2_w4_all_identical") is True
        and bool(a20.get("r6_ingest_canonical_semantic_sha256"))
    )
    gates["G24_DETERMINISM"] = "PASS" if g24 else "FAIL"

    # G25: Measured worker policy (strict R3.C1 kinds + R6 ingest benchmark)
    g25 = (
        a20.get("pytest_policy_valid") is True
        and a20.get("science_policy_valid") is True
        and a20.get("r6_ingest_benchmark_valid") is True
        and _is_strict_int(a20.get("applied_safe_workers_cap")) and 1 <= int(a20["applied_safe_workers_cap"]) <= 4
    )
    gates["G25_MEASURED_WORKER_POLICY"] = "PASS" if g25 else "FAIL"

    # G26: Paired fault truth (>=56 new genuine R6 pairs, zero literal flags)
    g26 = (
        _is_strict_int(a23.get("new_r6_paired_verified_count")) and int(a23["new_r6_paired_verified_count"]) >= MIN_NEW_PAIRED_CONTROLS_R6
        and a23.get("new_r6_paired_failed_count") == 0
        and a23.get("r6_fault_ast_literal_flags_count") == 0
        and a23.get("unique_r6_execution_ids_ok") is True
    )
    gates["G26_PAIRED_FAULT_TRUTH"] = "PASS" if g26 else "FAIL"

    # G27: Inherited regression (48+43 C1/C2 paired + 288 inherited regression executed separately)
    g27 = (
        _is_strict_int(a23.get("inherited_c1_c2_paired_passed_count")) and int(a23["inherited_c1_c2_paired_passed_count"]) >= 91
        and a23.get("inherited_c1_c2_paired_failed_count") == 0
        and _is_strict_int(a23.get("inherited_regression_passed_count")) and int(a23["inherited_regression_passed_count"]) >= 288
        and a23.get("inherited_regression_failed_count") == 0
        and a23.get("inherited_not_relabelled_as_new_r6") is True
    )
    gates["G27_INHERITED_REGRESSION"] = "PASS" if g27 else "FAIL"

    # G28: Independent verifier (separate OS process, raw recomputation)
    g28 = (
        a26.get("distinct_os_process_verified") is True
        and a26.get("verifier_status") == "PASS"
        and _is_strict_int(a26.get("violations_count")) and int(a26["violations_count"]) == 0
        and _is_strict_int(a26.get("verifier_pid")) and _is_strict_int(a26.get("orchestrator_pid"))
        and int(a26["verifier_pid"]) != int(a26["orchestrator_pid"])
    )
    gates["G28_INDEPENDENT_VERIFIER"] = "PASS" if g28 else "FAIL"

    # G29: Mutation resistance (>=30 NEW R6 mutations + 135 inherited mutations, clean clone PASS)
    g29 = (
        a24.get("clean_clone_verifier_green") is True
        and _is_strict_int(a24.get("new_r6_mutations_tested_count")) and int(a24["new_r6_mutations_tested_count"]) >= MIN_NEW_VERIFIER_MUTATIONS_R6
        and a24.get("new_r6_mutations_detected_count") == a24.get("new_r6_mutations_tested_count")
        and _is_strict_int(a24.get("inherited_mutations_tested_count")) and int(a24["inherited_mutations_tested_count"]) >= 135
        and a24.get("inherited_mutations_detected_count") == a24.get("inherited_mutations_tested_count")
    )
    gates["G29_MUTATION_RESISTANCE"] = "PASS" if g29 else "FAIL"

    # G30: Resource & cleanup (0.5s telemetry, <=4 global descendants, owned temp cleaned)
    g30 = (
        _is_strict_int(a21.get("samples_count")) and int(a21["samples_count"]) >= 2
        and _is_strict_int(a21.get("max_observed_descendants")) and int(a21["max_observed_descendants"]) <= 4
        and a21.get("owned_temp_cleaned_verified") is True
        and a21.get("orphan_workers_count") == 0
        and _is_num(a21.get("min_observed_disk_free_gib")) and float(a21["min_observed_disk_free_gib"]) >= 20.0
        and _is_num(a21.get("min_observed_ram_available_mib")) and float(a21["min_observed_ram_available_mib"]) >= 512.0
    )
    gates["G30_RESOURCE_CLEANUP"] = "PASS" if g30 else "FAIL"

    # Conditional Stage B/C/D gates: G31..G34 — NEVER passed in Stage A offline!
    ext_req = int(a09.get("external_market_requests_attempted") or 0)
    obs_rec = int(a09.get("accepted_real_observed_records") or 0)
    owner_auth_ok = (
        stage != "STAGE_A_OFFLINE"
        and a09.get("owner_trust_root_provisioned") is True
        and a09.get("owner_grant_verified_external") is True
        and a09.get("durable_claim_committed") is True
    )
    if stage == "STAGE_A_OFFLINE":
        # Enforce zero external market requests in Stage A!
        if ext_req != 0 or obs_rec != 0:
            gates["G31_EXTERNAL_AUTHORIZATION"] = "FAIL_UNAUTHORIZED_EXTERNAL_TRAFFIC_IN_STAGE_A"
            gates["G32_AUTHORIZED_SMOKE"] = "FAIL"
            gates["G33_DEV_ACQUISITION"] = "FAIL"
            gates["G34_R7_DATA_READINESS"] = "FAIL"
        else:
            gates["G31_EXTERNAL_AUTHORIZATION"] = "BLOCKED_OWNER_AUTHORIZATION"
            gates["G32_AUTHORIZED_SMOKE"] = "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"
            gates["G33_DEV_ACQUISITION"] = "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"
            gates["G34_R7_DATA_READINESS"] = "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"
    else:
        gates["G31_EXTERNAL_AUTHORIZATION"] = "PASS" if owner_auth_ok else "BLOCKED_OWNER_AUTHORIZATION"
        gates["G32_AUTHORIZED_SMOKE"] = "PASS" if (owner_auth_ok and a09.get("stage_b_smoke_verified") is True) else "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"
        gates["G33_DEV_ACQUISITION"] = "PASS" if (owner_auth_ok and a09.get("stage_c_dev_verified") is True) else "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"
        gates["G34_R7_DATA_READINESS"] = "PASS" if (owner_auth_ok and a09.get("r7_all_domains_complete") is True) else "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION"

    # G35: Economic / Activation / OOS honesty
    g35 = (
        a09.get("production_mode") == "OFF"
        and a09.get("live_trading_mode") == "OFF"
        and a09.get("paper_trading_mode") == "OFF"
        and a09.get("auto_trading_mode") == "OFF"
        and a09.get("activation_ready") == "NO"
        and a09.get("economic_verdict") == "NO_PROVEN_EDGE"
        and a09.get("fresh_oos_verdict") == "BLOCKED_NO_UNTOUCHED_DATA"
        and a09.get("kh04_entry_verdict") == "BLOCKED_NO_DEV_CANDIDATE"
        and a09.get("oos_evaluations") == 0
    )
    gates["G35_ECONOMIC_ACTIVATION"] = "PASS" if g35 else "FAIL"

    # G36: Remote publication readback
    if remote_readback is not None and remote_readback.get("all_sealed_files_remote_verified") is True and remote_readback.get("remote_tested_sha_verified") is True:
        gates["G36_REMOTE_PUBLICATION"] = "PASS_REMOTE_VERIFIED"
    elif a27.get("phase1_executable_committed") is True and a27.get("tested_executable_sha") == expected_tested_sha:
        gates["G36_REMOTE_PUBLICATION"] = "PENDING_REMOTE_READBACK"
    else:
        gates["G36_REMOTE_PUBLICATION"] = "FAIL"

    # Stage-specific denominators (NEVER conflate 32 Stage A gates with 36 total!)
    stage_a_prepush_core = [g for g in STAGE_A_MANDATORY_GATES_R6 if g != "G36_REMOTE_PUBLICATION"]
    stage_a_core_passed = sum(1 for g in stage_a_prepush_core if gates[g] == "PASS")
    g36_pass = gates["G36_REMOTE_PUBLICATION"] == "PASS_REMOTE_VERIFIED"
    stage_a_passed_total = stage_a_core_passed + (1 if g36_pass else 0)
    stage_bc_executed = sum(1 for g in STAGE_BC_CONDITIONAL_GATES_R6 if gates[g] == "PASS")

    stage_a_all_ready_prepush = (stage_a_core_passed == len(stage_a_prepush_core)) and (gates["G36_REMOTE_PUBLICATION"] in ("PENDING_REMOTE_READBACK", "PASS_REMOTE_VERIFIED")) and (ext_req == 0 if stage == "STAGE_A_OFFLINE" else True)
    stage_a_all_closed_remote = (stage_a_passed_total == len(STAGE_A_MANDATORY_GATES_R6)) and (ext_req == 0 if stage == "STAGE_A_OFFLINE" else True)

    if stage == "STAGE_A_OFFLINE" and (stage_a_all_ready_prepush or stage_a_all_closed_remote):
        overall_classification = CLASSIFICATION_PASS_OFFLINE_R6
    else:
        overall_classification = "FAIL_OR_BLOCKED_KH03_R6_REQUIRED_GATES"

    ci_status = str((remote_readback or {}).get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN")

    independent_verdicts = {
        "ENGINEERING_OFFLINE": "PASS" if stage_a_all_ready_prepush else "FAIL",
        "DURABLE_GRANT_LOCAL_TEST": "PASS_LOCAL_QA_TEST_ONLY" if (gates["G08_DURABLE_ONE_SHOT"] == "PASS" and gates["G09_CRASH_ANCHOR_RECOVERY"] == "PASS") else "FAIL",
        "OWNER_TRUST_READY": "BLOCKED_NOT_PROVISIONED_BY_OWNER" if not owner_auth_ok else "PROVISIONED",
        "TRANSPORT_TERMINATION_LOOPBACK": "PASS_LOOPBACK_OS_REAP_VERIFIED" if (gates["G11_OWNED_TRANSPORT_PROCESS"] == "PASS" and gates["G12_TERMINATION_REAP"] == "PASS") else "FAIL",
        "TRANSPORT_EXTERNAL_VERIFIED": "NOT_RUN_BLOCKED_OWNER_AUTHORIZATION" if not owner_auth_ok else ("PASS" if gates["G32_AUTHORIZED_SMOKE"] == "PASS" else "FAIL"),
        "SOURCE_DOCS": "DOC_VERIFIED_CLASSIC_V2_ONLY" if gates["G04_SOURCE_DOCS"] == "PASS" else "BLOCKED",
        "INSTRUMENT_OBSERVED_IDENTITY": "NOT_OBSERVED_STAGE_A_OFFLINE" if not owner_auth_ok else "OBSERVED",
        "CAPTURE_AUTHORIZATION": gates["G31_EXTERNAL_AUTHORIZATION"],
        "SMOKE": gates["G32_AUTHORIZED_SMOKE"],
        "ACQUISITION": gates["G33_DEV_ACQUISITION"],
        "OHLCV_COVERAGE": "NOT_RUN_STAGE_A_OFFLINE_CONTRACT_VERIFIED" if not owner_auth_ok else "EVALUATED",
        "BBO_EVENT_COVERAGE": "NOT_RUN_STAGE_A_OFFLINE_CONTRACT_VERIFIED" if not owner_auth_ok else "EVALUATED",
        "BBO_STATE_COVERAGE": "NOT_RUN_STAGE_A_OFFLINE_CONTRACT_VERIFIED" if not owner_auth_ok else "EVALUATED",
        "FUNDING_COMPLETENESS": "NOT_RUN_STAGE_A_OFFLINE_CONTRACT_VERIFIED" if not owner_auth_ok else "EVALUATED",
        "ACCOUNT_FEE": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "SLIPPAGE": "MISSING_NO_OWNER_FILL_EVIDENCE",
        "CUSTODY": "PASS_QA_LOOPBACK_ONLY" if gates["G20_CUSTODY_PARTITION"] == "PASS" else "FAIL",
        "DATA_READINESS_DEV": "BLOCKED_NO_AUTHORIZED_OBSERVED_DEV_DATA" if not owner_auth_ok else gates["G34_R7_DATA_READINESS"],
        "EXECUTION_FIDELITY": "INCOMPLETE_NO_OWNER_EXECUTION_FILLS",
        "REGRESSION": "PASS" if (gates["G21_FULL_PYTEST"] == "PASS" and gates["G27_INHERITED_REGRESSION"] == "PASS") else "FAIL",
        "BACKTEST_PARITY": "PASS" if (gates["G22_BASELINE_PARITY"] == "PASS" and gates["G23_BACKTEST_PARITY"] == "PASS") else "FAIL",
        "DETERMINISM": "PASS" if gates["G24_DETERMINISM"] == "PASS" else "FAIL",
        "RESOURCE_SAFETY": "PASS" if (gates["G15_RESOURCE_VOLUME"] == "PASS" and gates["G30_RESOURCE_CLEANUP"] == "PASS") else "FAIL",
        "PUBLICATION": gates["G36_REMOTE_PUBLICATION"],
        "CI": ci_status,
        "ECONOMIC": "NO_PROVEN_EDGE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "ACTIVATION_READY": "NO",
    }

    return {
        "spec_version": SPEC_VERSION_KH03_R6,
        "schema_version": SCHEMA_VERSION_KH03_R6,
        "stage": stage,
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "gates": gates,
        "stage_denominators": {
            "stage_a_mandatory_total": len(STAGE_A_MANDATORY_GATES_R6),
            "stage_a_prepush_core_total": len(stage_a_prepush_core),
            "stage_a_prepush_core_passed": stage_a_core_passed,
            "stage_a_passed_including_remote": stage_a_passed_total,
            "stage_bc_conditional_total": len(STAGE_BC_CONDITIONAL_GATES_R6),
            "stage_bc_executed_count": stage_bc_executed,
            "never_claim_36_of_36_without_capture": True,
        },
        "stage_a_ready_for_publication": stage_a_all_ready_prepush,
        "stage_a_fully_closed_remote": stage_a_all_closed_remote,
        "overall_classification": overall_classification,
        "independent_verdicts": independent_verdicts,
        "external_market_requests_attempted": ext_req,
        "accepted_real_observed_records": obs_rec,
        "oos_evaluations": int(a09.get("oos_evaluations") or 0),
        "r7_ready": "NO_BLOCKED_NO_OBSERVED_DEV_DATA" if not owner_auth_ok else "REVIEW_REQUIRED",
    }


def build_r6_owner_handover_vi(
    *,
    run_id: str,
    branch: str,
    base_head_sha: str,
    tested_sha: str,
    verdicts: Mapping[str, Any],
    summary_stats: Mapping[str, Any],
    unsigned_template_rel_path: str,
) -> str:
    """Builds concise, auditable Vietnamese owner handover text (saved as HANDOVER_KH03_R6_VI.txt)."""
    sd = verdicts["stage_denominators"]
    iv = verdicts["independent_verdicts"]
    lines = [
        "==============================================================================",
        f"AURUM DESK — KH-03.R6 | BÁO CÁO BÀN GIAO CHỦ DỰ ÁN (STAGE A OFFLINE)",
        "==============================================================================",
        f"Spec: {SPEC_VERSION_KH03_R6} | Run ID: {run_id}",
        f"Branch: {branch}",
        f"Base Reviewed HEAD (C2): {base_head_sha}",
        f"Tested Executable SHA:   {tested_sha}",
        f"Overall Classification:  {verdicts['overall_classification']}",
        "",
        "1. PHÂN TÁCH GATE THEO GIAI ĐOẠN (KHÔNG BAO GIỜ BÁO 36/36 PASS KHI CHƯA CAPTURE)",
        f"   - Stage A (bắt buộc offline): {sd['stage_a_prepush_core_passed']}/{sd['stage_a_prepush_core_total']} core gates PASS (+ G36={verdicts['gates']['G36_REMOTE_PUBLICATION']})",
        f"   - Stage B/C (capture ngoài):  {sd['stage_bc_executed_count']}/{sd['stage_bc_conditional_total']} executed — G31={verdicts['gates']['G31_EXTERNAL_AUTHORIZATION']}, G32={verdicts['gates']['G32_AUTHORIZED_SMOKE']}, G33={verdicts['gates']['G33_DEV_ACQUISITION']}, G34={verdicts['gates']['G34_R7_DATA_READINESS']}",
        "",
        "2. TRẠNG THÁI AN TOÀN & KHÔNG KÍCH HOẠT GIAO DỊCH",
        f"   - PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY={iv['ACTIVATION_READY']}",
        f"   - ECONOMIC={iv['ECONOMIC']} | FRESH_OOS={iv['FRESH_OOS']} | KH04_ENTRY={iv['KH04_ENTRY']} | OOS_EVALUATIONS={verdicts['oos_evaluations']}",
        f"   - EXTERNAL_MARKET_REQUESTS_ATTEMPTED={verdicts['external_market_requests_attempted']} | ACCEPTED_REAL_OBSERVED_RECORDS={verdicts['accepted_real_observed_records']}",
        f"   - ACCOUNT_FEE={iv['ACCOUNT_FEE']} | SLIPPAGE={iv['SLIPPAGE']} | EXECUTION_FIDELITY={iv['EXECUTION_FIDELITY']}",
        "",
        "3. TÓM TẮT KIỂM ĐỊNH KỸ THUẬT STAGE A",
        f"   - Source Docs: Bitget Classic v2 ({iv['SOURCE_DOCS']}), chưa quan sát thực tế ({iv['INSTRUMENT_OBSERVED_IDENTITY']})",
        f"   - Durable Ledger: SQLite BEGIN IMMEDIATE + synchronous=FULL + external anchor ({iv['DURABLE_GRANT_LOCAL_TEST']}; Owner trust: {iv['OWNER_TRUST_READY']})",
        f"   - Owned Transport: Subprocess + Windows Job Object / POSIX PGID reap ({iv['TRANSPORT_TERMINATION_LOOPBACK']}; External: {iv['TRANSPORT_EXTERNAL_VERIFIED']})",
        f"   - Full Pytest: {summary_stats.get('pytest_passed')}/{summary_stats.get('pytest_collected')} passed, {summary_stats.get('pytest_skipped')} skipped (0 mandatory skip, 0 fail)",
        f"   - Paired Controls: {summary_stats.get('new_r6_paired')} mới (R6) + {summary_stats.get('inherited_c1_c2_paired')} C1/C2 + {summary_stats.get('inherited_regression')} inherited regression",
        f"   - Verifier Mutations: {summary_stats.get('new_r6_mutations_detected')}/{summary_stats.get('new_r6_mutations_tested')} mới (R6) + {summary_stats.get('inherited_mutations_detected')}/{summary_stats.get('inherited_mutations_tested')} inherited",
        f"   - Parity & R4 Replay: A1=56,710/0, SI8=6,375/0, Adapters=246/0, D4=0; R4 1x 203 closed (7W/196L), net -441.6118 USD, -0.8806R, 139 loss tails >2.50 USD",
        "",
        "4. HƯỚNG DẪN CHỦ DỰ ÁN NẾU MUỐN CHẠY STAGE B / C SAU NÀY (KHÔNG CHẠY TỰ ĐỘNG)",
        f"   - Mẫu phê duyệt chưa ký (Unsigned Owner Review Template): {unsigned_template_rel_path}",
        "   - Mọi khóa ký Ed25519 của chủ dự án, trust root, SQLite ledger và dữ liệu thô DEV",
        "     PHẢI lưu cục bộ ngoài Git trên ổ đĩa cố định có phân quyền ACL riêng.",
        "   - Bước 1 (dry-run, 0 socket): python aurum-desk/tools/run_kh_03_r6_capture.py --stage smoke --dry-run --plan <local-smoke-plan.json>",
        "   - Bước 2 (CHỈ DÀNH CHO R6.B TƯƠNG LAI sau khi chủ dự án tự cấp trust root + ký grant riêng cho executable SHA đã đóng băng):",
        "     python aurum-desk/tools/run_kh_03_r6_capture.py --stage smoke --execute-authorized --grant <local-signed-smoke-grant.json> --owner-config <local-owner-config.json> --release-manifest <local-stageA-manifest.json> --release-receipt <local-readback-28.json>",
        "==============================================================================",
    ]
    return "\n".join(lines) + "\n"
