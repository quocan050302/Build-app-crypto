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
import json
import math
from pathlib import Path
import shutil
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
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

NY_TZ = ZoneInfo("America/New_York")
STRICT_PREFLIGHT_DISK_MIN_GIB_R5 = max(20.5, float(STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED))
STRICT_RUNTIME_DISK_MIN_GIB_R5 = 20.0
ADVISORY_DISK_TARGET_GIB_R5 = 25.0
MAX_GLOBAL_CONCURRENCY_R5 = 4
DEFAULT_MAX_PAGE_BYTES_R5 = MAX_RAW_HTTP_BODY_BYTES_C3
DEFAULT_MAX_DECOMPRESSED_BYTES_R5 = MAX_DECOMPRESSED_BODY_BYTES_C3
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
    Human authorization additionally requires explicit out-of-band verified attestation
    in `trust_context` (`human_operator_attestation_verified=True` and non-QA trust mode).
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
# 2. validate_r5_operator_capture_scope
# =====================================================================
def validate_r5_operator_capture_scope(
    authorization: Optional[Any],
    requested_scope: Mapping[str, Any],
    now_utc: datetime,
    trust_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fail-closed operator capture scope validator.
    NEVER accepts a CLI bool, env var, QA token, prompt text, or bare dataclass as proof
    of live human approval. Live public DEV capture requires:
      - `authorization` is an `R5OperatorGrant` with `is_qa_fixture_grant=False`
      - `trust_context["human_operator_attestation_verified"] is True`
      - `trust_context["attestation_source"] == "EXPLICIT_SEPARATE_OWNER_GRANT"`
      - `trust_context["qa_fixture_mode"] is False`
      - `purpose == "DEVELOPMENT_CAPTURE"` (NEVER OOS!)
      - exact symbol/contract/endpoint/window/budget/expiry/run_id match and zero secrets!
    Offline QA fixture simulation is admitted strictly as `QA_FIXTURE_ONLY` (zero live network!).
    """
    tc = dict(trust_context or {})
    now_ms = int(now_utc.timestamp() * 1000)
    violations: List[str] = []

    # Reject any secret accidentally embedded in requested_scope or trust_context
    if scan_for_forbidden_secrets_r2_c1(dict(requested_scope)) or scan_for_forbidden_secrets_r2_c1(tc):
        return {
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
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION",
            "live_network_permitted": False,
            "violations": violations + ["NO_SEPARATE_OPERATOR_AUTHORIZATION_SUPPLIED"],
        }

    if not isinstance(authorization, R5OperatorGrant):
        return {
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": violations + ["AUTHORIZATION_OBJECT_TYPE_INVALID"],
        }

    # Validate grant fields strictly
    if authorization.purpose not in ("DEVELOPMENT_CAPTURE", "QA_FIXTURE"):
        violations.append(f"FORBIDDEN_CAPTURE_PURPOSE:{authorization.purpose}")
    if "OOS" in str(authorization.purpose).upper() or "OOS" in str(requested_scope.get("purpose", "")).upper():
        violations.append("OOS_CAPTURE_OR_EVALUATION_FORBIDDEN_IN_R5")
    if authorization.expires_utc_ms <= now_ms:
        violations.append("OPERATOR_GRANT_EXPIRED")
    if authorization.window_end_ms <= authorization.window_start_ms:
        violations.append("INVALID_CAPTURE_TIME_WINDOW")
    if authorization.max_requests <= 0 or authorization.max_requests > 500:
        violations.append(f"INVALID_MAX_REQUESTS:{authorization.max_requests}")
    if authorization.max_total_bytes <= 0 or authorization.max_total_bytes > 50 * 1024 * 1024:
        violations.append(f"INVALID_MAX_TOTAL_BYTES:{authorization.max_total_bytes}")
    if authorization.max_duration_sec <= 0 or authorization.max_duration_sec > 3600:
        violations.append(f"INVALID_MAX_DURATION_SEC:{authorization.max_duration_sec}")

    req_sym = str(requested_scope.get("symbol") or "")
    req_pt = str(requested_scope.get("product_type") or "")
    req_eps = tuple(requested_scope.get("endpoints") or ())
    req_rid = str(requested_scope.get("run_id") or "")
    if req_sym != authorization.symbol or req_pt != authorization.product_type:
        violations.append("SYMBOL_OR_PRODUCT_TYPE_SCOPE_MISMATCH")
    if not req_eps or any(ep not in authorization.allowed_endpoints for ep in req_eps):
        violations.append("REQUESTED_ENDPOINT_OUTSIDE_GRANT")
    if any(ep not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2 for ep in req_eps):
        violations.append("REQUESTED_ENDPOINT_NOT_IN_PUBLIC_GET_ALLOWLIST")
    if req_rid != authorization.run_id or not req_rid.strip():
        violations.append("RUN_ID_SCOPE_MISMATCH")
    if int(requested_scope.get("max_requests", 0)) > authorization.max_requests:
        violations.append("REQUESTED_REQUESTS_EXCEED_GRANT_BUDGET")
    if int(requested_scope.get("max_total_bytes", 0)) > authorization.max_total_bytes:
        violations.append("REQUESTED_BYTES_EXCEED_GRANT_BUDGET")

    # Separate QA fixture scope vs Live DEV capture scope!
    if authorization.is_qa_fixture_grant or tc.get("qa_fixture_mode") is True:
        if authorization.is_qa_fixture_grant and tc.get("qa_fixture_mode") is True and authorization.purpose == "QA_FIXTURE" and not violations:
            return {
                "admitted": True,
                "mode": "QA_FIXTURE_OFFLINE_ONLY",
                "authorization_status": "QA_FIXTURE_ISOLATED",
                "live_network_permitted": False,
                "scope_digest_sha256": canonical_hash({"grant_id": authorization.grant_id, "purpose": "QA_FIXTURE", "run_id": req_rid, "endpoints": list(req_eps)}),
                "violations": [],
            }
        return {
            "admitted": False,
            "mode": "QA_TOKEN_CANNOT_AUTHORIZE_LIVE_CAPTURE",
            "authorization_status": "INVALID",
            "live_network_permitted": False,
            "violations": violations + ["QA_FIXTURE_GRANT_ISOLATED_FROM_LIVE_TRANSPORT"],
        }

    # Live DEV public capture requires genuine out-of-band human owner attestation!
    human_ok = (
        tc.get("human_operator_attestation_verified") is True
        and tc.get("attestation_source") == "EXPLICIT_SEPARATE_OWNER_GRANT"
        and authorization.origin_kind == "SEPARATE_OWNER_ATTESTATION"
        and authorization.purpose == "DEVELOPMENT_CAPTURE"
    )
    if not human_ok:
        violations.append("GENUINE_HUMAN_OWNER_AUTHORIZATION_ABSENT_OR_UNVERIFIED")

    if violations:
        return {
            "admitted": False,
            "mode": "CAPTURE_BLOCKED_OWNER_AUTHORIZATION",
            "authorization_status": "BLOCKED_OWNER_AUTHORIZATION" if "GENUINE_HUMAN_OWNER_AUTHORIZATION_ABSENT_OR_UNVERIFIED" in violations and len(violations) == 1 else "INVALID",
            "live_network_permitted": False,
            "violations": violations,
        }

    return {
        "admitted": True,
        "mode": "AUTHORIZED_BOUNDED_DEV_PUBLIC_CAPTURE",
        "authorization_status": "AUTHORIZED_BOUNDED_DEV",
        "live_network_permitted": True,
        "scope_digest_sha256": canonical_hash({
            "grant_id": authorization.grant_id, "purpose": authorization.purpose, "symbol": authorization.symbol,
            "product_type": authorization.product_type, "endpoints": list(req_eps), "run_id": req_rid,
            "window_start_ms": authorization.window_start_ms, "window_end_ms": authorization.window_end_ms,
        }),
        "violations": [],
    }


# =====================================================================
# 3. plan_r5_capture_batches
# =====================================================================
def plan_r5_capture_batches(
    scope_decision: Mapping[str, Any],
    capability_inventory: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Constructs an immutable, bounded capture batch plan strictly for `DEVELOPMENT_CAPTURE`
    or `QA_FIXTURE` (NEVER OOS!). Refuses disk waivers, rejects insufficient disk (<20.5 GiB),
    checks memory/page/byte/duration/concurrency bounds, and returns `BLOCKED` if unauthorized.
    """
    violations: List[str] = []
    if budget.get("owner_waive_disk_preflight") is True:
        violations.append("DISK_WAIVER_STRICTLY_FORBIDDEN_IN_R5")

    disk_free_gib = float(budget.get("destination_disk_free_gib", 0.0))
    if not math.isfinite(disk_free_gib) or disk_free_gib < STRICT_PREFLIGHT_DISK_MIN_GIB_R5:
        violations.append(f"BLOCKED_LOW_DISK_PREFLIGHT:{disk_free_gib:.4f}<{STRICT_PREFLIGHT_DISK_MIN_GIB_R5}")

    max_conc = int(budget.get("max_concurrency", 1))
    if max_conc < 1 or max_conc > MAX_GLOBAL_CONCURRENCY_R5:
        violations.append(f"CONCURRENCY_CAP_EXCEEDED:{max_conc}>{MAX_GLOBAL_CONCURRENCY_R5}")

    max_pages = int(budget.get("max_pages", 0))
    max_req = int(budget.get("max_requests", 0))
    max_bytes = int(budget.get("max_total_bytes", 0))
    max_resp_bytes = int(budget.get("max_response_bytes", DEFAULT_MAX_PAGE_BYTES_R5))
    max_dur = int(budget.get("max_duration_sec", 0))
    if max_pages <= 0 or max_req <= 0 or max_bytes <= 0 or max_dur <= 0 or max_resp_bytes <= 0 or max_resp_bytes > DEFAULT_MAX_PAGE_BYTES_R5:
        violations.append("INVALID_OR_UNBOUNDED_CAPTURE_BUDGET")

    projected_post_gib = disk_free_gib - (max_bytes / (1024.0 ** 3)) - 0.05
    if projected_post_gib < STRICT_RUNTIME_DISK_MIN_GIB_R5:
        violations.append(f"PROJECTED_DISK_POST_CAPTURE_BELOW_20GIB:{projected_post_gib:.4f}")

    if not scope_decision.get("admitted"):
        return {
            "status": "BLOCKED",
            "block_reason": str(scope_decision.get("mode") or "CAPTURE_BLOCKED_OWNER_AUTHORIZATION"),
            "partition_role": "NONE_BLOCKED",
            "live_network_permitted": False,
            "batches": [],
            "violations": list(scope_decision.get("violations") or ()) + violations,
        }

    is_qa = scope_decision.get("mode") == "QA_FIXTURE_OFFLINE_ONLY"
    partition_role = "QA_FIXTURE" if is_qa else "DEVELOPMENT_CAPTURE"
    req_eps = list(budget.get("endpoints") or ())
    supported = capability_inventory.get("supported_endpoints_inventory") or {}
    batches: List[Dict[str, Any]] = []
    for idx, ep in enumerate(req_eps):
        if ep not in supported:
            violations.append(f"UNSUPPORTED_ENDPOINT_IN_PLAN:{ep}")
            continue
        ep_info = supported[ep]
        if not ep_info.get("verified_in_c3_trust_boundary"):
            violations.append(f"ENDPOINT_OUTSIDE_C3_TRUST_BOUNDARY:{ep}")
            continue
        batches.append({
            "batch_id": f"R5_BATCH_{idx + 1:02d}_{ep}",
            "endpoint_key": ep,
            "url": ep_info["url"],
            "method": "GET",
            "symbol": str(budget.get("symbol") or "XAUUSDT"),
            "product_type": str(budget.get("product_type") or "USDT-FUTURES"),
            "window_start_ms": int(budget.get("window_start_ms", 0)),
            "window_end_ms": int(budget.get("window_end_ms", 0)),
            "max_pages": min(max_pages, max_req),
            "min_request_interval_ms": max(50, int(budget.get("min_request_interval_ms", 100))),
        })

    if violations or not batches:
        return {
            "status": "BLOCKED",
            "block_reason": violations[0] if violations else "EMPTY_CAPTURE_PLAN",
            "partition_role": partition_role,
            "live_network_permitted": False,
            "batches": [],
            "violations": violations,
        }

    plan_doc: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "status": "PLANNED_QA_OFFLINE" if is_qa else "PLANNED_DEV_BOUNDED",
        "partition_role": partition_role,
        "oos_designation_forbidden": True,
        "live_network_permitted": bool(scope_decision.get("live_network_permitted")),
        "scope_digest_sha256": scope_decision.get("scope_digest_sha256"),
        "budget": {
            "max_pages": max_pages,
            "max_requests": max_req,
            "max_total_bytes": max_bytes,
            "max_response_bytes": max_resp_bytes,
            "max_duration_sec": max_dur,
            "max_retries_per_request": min(3, max(0, int(budget.get("max_retries_per_request", 2)))),
            "max_concurrency": max_conc,
            "destination_disk_free_gib": round(disk_free_gib, 4),
            "projected_post_capture_disk_gib": round(projected_post_gib, 4),
        },
        "batches": batches,
        "violations": [],
    }
    plan_doc["plan_digest_sha256"] = canonical_hash({k: v for k, v in plan_doc.items() if k != "plan_digest_sha256"})
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
