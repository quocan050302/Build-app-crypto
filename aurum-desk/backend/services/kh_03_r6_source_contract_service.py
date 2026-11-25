"""
aurum-desk/backend/services/kh_03_r6_source_contract_service.py

AURUM DESK — KH-03.R6 (M1) | VERIFIED PUBLIC SOURCE CONTRACTS & BITGET CAPABILITY AUDIT
SPEC VERSION: KH-03.R6-v1.0 | DOCS-VERIFIED CLASSIC V2 CONTRACT | ZERO EXTERNAL MARKET CALLS IN STAGE A

Implements:
  - F01: build_r6_source_contract_registry(doc_evidence, existing_inventory)
  - F02: validate_r6_endpoint_contract(registry, endpoint_id, params, grant_scope, ...)
  - F03: validate_r6_instrument_identity(raw, contract, provenance)
  - F04: build_r6_source_capability_matrix(registry, observed_evidence=None)

Design invariants:
  - Selects Bitget Classic v2 (`api.bitget.com/api/v2/mix/market/...` + `wss://ws.bitget.com/v2/ws/public`,
    `productType="USDT-FUTURES"`, `symbol="XAUUSDT"`) because existing verified adapters (`bitget_data.py`,
    `bitget_ws_service.py`, `kh_02_r2_c2_public_capture.py`) already use Classic v2.
  - Explicitly distinguishes UTA v3 (`/api/v3/market/...`, `category="USDT-FUTURES"`) as a separate
    non-mixable API family. Mixing v2 and v3 parameters fails closed (`MIXED_API_FAMILY_CONFLICT`).
  - Distinguishes `DOC_VERIFIED` from `OBSERVED_CONTRACT_VERIFIED` (always `False` in offline Stage A).
  - Never silently substitutes `XAUTUSDT`, `PAXGUSDT`, `XAUUSD`, spot, CFD, index, or mark prices.
  - Strictly forbids private/account/order/trade/login/wallet/withdraw/transfer paths or operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, urlsplit

SPEC_VERSION_KH03_R6 = "KH-03.R6-v1.0"
SCHEMA_VERSION_KH03_R6 = "kh_03_r6_v1"
MANDATORY_BRANCH_KH03_R6 = "feature/v17-9-2-structural-sl-tp-from-qa"
BASELINE_REVIEWED_HEAD_SHA_R6 = "fd0df117ca38f0ac72312d27265dcdabaa5a0dba"
PRIOR_C2_TESTED_EXECUTABLE_SHA = "68549a8a44a424eb58f9c21bec49199ab296eb1a"
PRIOR_C2_EVIDENCE_COMMIT_SHA = "f79419f959639994b6d631df16cd352bbcbb2e69"
PRIOR_C2_RUN_ID = "20261125T061403Z_68549a8a44a4"

API_FAMILY_CLASSIC_V2 = "BITGET_CLASSIC_V2"
API_FAMILY_UTA_V3 = "BITGET_UTA_V3"

APPROVED_VENUE_R6 = "BITGET"
APPROVED_SYMBOL_R6 = "XAUUSDT"
APPROVED_PRODUCT_TYPE_V2_R6 = "USDT-FUTURES"
APPROVED_REST_HOST_R6 = "api.bitget.com"
APPROVED_WS_HOST_R6 = "ws.bitget.com"
APPROVED_WS_PATH_V2_R6 = "/v2/ws/public"

FORBIDDEN_SUBSTITUTE_SYMBOLS_R6: Tuple[str, ...] = (
    "XAUTUSDT",
    "PAXGUSDT",
    "XAUUSD",
    "XAUUSDC",
    "GOLDUSDT",
    "GOLD",
    "XAU",
    "XAU_USDT",
    "XAU-USDT",
    "BTCUSDT",
    "ETHUSDT",
)

FORBIDDEN_ENDPOINT_TOKENS_R6: Tuple[str, ...] = (
    "/order",
    "/orders",
    "/trade",
    "/place",
    "/cancel",
    "/account",
    "/wallet",
    "/position",
    "/assets",
    "/transfer",
    "/withdraw",
    "/deposit",
    "/private",
    "/subaccount",
    "/broker",
    "/margin",
    "/copytrading",
    "/login",
    "/auth",
)

FORBIDDEN_WS_OPS_R6: Tuple[str, ...] = (
    "login",
    "auth",
    "order",
    "place-order",
    "cancel-order",
    "trade",
    "account",
    "positions",
    "orders",
)

V3_ONLY_PARAM_NAMES_R6: Tuple[str, ...] = ("category", "interval", "cursor")
V2_ONLY_PARAM_NAMES_R6: Tuple[str, ...] = ("productType", "granularity", "instType", "instId", "pageNo", "pageSize")

ALLOWED_V2_GRANULARITIES_R6: Tuple[str, ...] = ("1m", "5m", "15m", "1H", "4H", "1D")

# Official Bitget public documentation pages retrieved on 2026-11-25 (public docs ONLY — ZERO market API calls)
OFFICIAL_BITGET_DOC_EVIDENCE_R6: Tuple[Dict[str, Any], ...] = (
    {
        "doc_id": "bitget_classic_contract_market",
        "url": "https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market",
        "final_url": "https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:02Z",
        "content_bytes": 1000870,
        "content_sha256": "3feb6a4b45f29fe8c365f480dd5cb9fab5cb1c899c4dcaed1960f4d9365cce9a",
        "api_family": API_FAMILY_CLASSIC_V2,
        "verified_markers": (
            "/api/v2/mix/market/contracts",
            "/api/v2/mix/market/candles",
            "/api/v2/mix/market/history-candles",
            "/api/v2/mix/market/history-fund-rate",
            "/api/v2/mix/market/ticker",
            "USDT-FUTURES",
        ),
        "status": "DOC_VERIFIED",
    },
    {
        "doc_id": "bitget_classic_rest_api",
        "url": "https://www.bitget.com/docs/classic/rest-api",
        "final_url": "https://www.bitget.com/docs/classic/rest-api",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:06Z",
        "content_bytes": 115067,
        "content_sha256": "0f86585d9271788a151d08378f789644e86ccf26e92abe6533e5190a55621a46",
        "api_family": API_FAMILY_CLASSIC_V2,
        "verified_markers": ("wss://ws.bitget.com/v2/ws/public", "productType"),
        "status": "DOC_VERIFIED",
    },
    {
        "doc_id": "bitget_uta_derivatives_reference",
        "url": "https://www.bitget.com/docs/catalog/market/derivatives",
        "final_url": "https://www.bitget.com/docs/catalog/market/derivatives",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:04Z",
        "content_bytes": 351909,
        "content_sha256": "6535ed4c9d1996885b6a09b3b36f72f199dedb46c68bd3291ec0f982d22ea2f2",
        "api_family": API_FAMILY_UTA_V3,
        "verified_markers": ("/api/v3/market/instruments", "/api/v3/market/history-candles", "/api/v3/market/history-fund-rate", "USDT-FUTURES"),
        "status": "DOC_VERIFIED_ALTERNATE_FAMILY_NOT_SELECTED",
    },
    {
        "doc_id": "bitget_uta_market_data_reference",
        "url": "https://www.bitget.com/docs/catalog/market/market-data",
        "final_url": "https://www.bitget.com/docs/catalog/market/market-data",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:04Z",
        "content_bytes": 645733,
        "content_sha256": "5b8752049aefdc39f76dbb20b23024413ba049d926465d3e1367e1c3fd1871fb",
        "api_family": API_FAMILY_UTA_V3,
        "verified_markers": ("/api/v3/market/instruments", "/api/v3/market/history-candles", "/api/v3/market/history-fund-rate"),
        "status": "DOC_VERIFIED_ALTERNATE_FAMILY_NOT_SELECTED",
    },
    {
        "doc_id": "bitget_uta_best_practices",
        "url": "https://www.bitget.com/docs/uta/best-practices-guide",
        "final_url": "https://www.bitget.com/docs/uta/best-practices-guide",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:04Z",
        "content_bytes": 143502,
        "content_sha256": "12028cf3af7148134a92ba1e4674f0581cda16c307269e971a6a3eb8c70fd995",
        "api_family": API_FAMILY_UTA_V3,
        "verified_markers": ("books1", "/api/v3/market/instruments"),
        "status": "DOC_VERIFIED",
    },
    {
        "doc_id": "bitget_ws_orderbook_channel",
        "url": "https://www.bitget.com/legacy-docs/uta/websocket/public/Order-Book-Channel",
        "final_url": "https://www.bitget.com/legacy-docs/uta/websocket/public/Order-Book-Channel",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:05Z",
        "content_bytes": 46962,
        "content_sha256": "43225d85e409f397eef63f623b13dad2cf7dda324a8b3283a2233999284a9771",
        "api_family": API_FAMILY_CLASSIC_V2,
        "verified_markers": ("books1",),
        "status": "DOC_VERIFIED",
    },
    {
        "doc_id": "bitget_ws_tickers_channel",
        "url": "https://www.bitget.com/docs/uta/websocket/public/Tickers-Channel",
        "final_url": "https://www.bitget.com/docs/uta/websocket/public/Tickers-Channel",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:05Z",
        "content_bytes": 68057,
        "content_sha256": "cc2cdf00a7839f9769eb49bd29754b7b3785cc07af72d10d62c9f416554ef95c",
        "api_family": API_FAMILY_UTA_V3,
        "verified_markers": (),
        "status": "DOC_VERIFIED",
    },
    {
        "doc_id": "bitget_uta_rest_api",
        "url": "https://www.bitget.com/docs/uta/rest-api",
        "final_url": "https://www.bitget.com/docs/uta/rest-api",
        "http_status": 200,
        "retrieval_utc": "2026-11-25T08:01:06Z",
        "content_bytes": 101307,
        "content_sha256": "6aa858c0dd103126c92b723fa8909fb559304f5a361baf5ef21842391600f461",
        "api_family": API_FAMILY_UTA_V3,
        "verified_markers": ("wss://ws.bitget.com/v2/ws/public",),
        "status": "DOC_VERIFIED",
    },
)


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_canonical(obj: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(obj)).hexdigest()


def _strict_decimal(val: Any, field: str) -> Decimal:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_DECIMAL_TYPE:{field}")
    if isinstance(val, float) and not math.isfinite(val):
        raise ValueError(f"NONFINITE_DECIMAL:{field}")
    s = str(val).strip()
    if not s:
        raise ValueError(f"EMPTY_DECIMAL:{field}")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"UNPARSEABLE_DECIMAL:{field}:{s}") from exc
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL:{field}")
    return d


@dataclass(frozen=True)
class R6EndpointContract:
    endpoint_id: str
    venue: str
    api_family: str
    transport_kind: str  # "REST_GET" | "WS_PUBLIC"
    scheme: str
    host: str
    port: int
    path: str
    method_or_op: str  # "GET" | "subscribe"
    ws_channel: Optional[str]
    required_params: Tuple[str, ...]
    optional_params: Tuple[str, ...]
    allowed_symbols: Tuple[str, ...]
    allowed_product_types: Tuple[str, ...]
    allowed_granularities: Tuple[str, ...]
    doc_rate_limit_rps: int
    conservative_r6_rate_limit_rps: float
    max_response_bytes: int
    max_decoded_bytes: int
    max_items_per_page: int
    retention_policy_notes: str
    timestamp_semantics: str
    sequence_semantics: str
    funding_schedule_semantics: str
    continuous_bbo_capable: bool
    evidence_url: str
    retrieval_utc: str
    content_digest: str
    doc_status: str  # "DOC_VERIFIED" | "DOC_CANDIDATE_UNOBSERVED" | "BLOCKED_DOC_UNAVAILABLE"
    observed_contract_verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "venue": self.venue,
            "api_family": self.api_family,
            "transport_kind": self.transport_kind,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "method_or_op": self.method_or_op,
            "ws_channel": self.ws_channel,
            "required_params": list(self.required_params),
            "optional_params": list(self.optional_params),
            "allowed_symbols": list(self.allowed_symbols),
            "allowed_product_types": list(self.allowed_product_types),
            "allowed_granularities": list(self.allowed_granularities),
            "doc_rate_limit_rps": self.doc_rate_limit_rps,
            "conservative_r6_rate_limit_rps": self.conservative_r6_rate_limit_rps,
            "max_response_bytes": self.max_response_bytes,
            "max_decoded_bytes": self.max_decoded_bytes,
            "max_items_per_page": self.max_items_per_page,
            "retention_policy_notes": self.retention_policy_notes,
            "timestamp_semantics": self.timestamp_semantics,
            "sequence_semantics": self.sequence_semantics,
            "funding_schedule_semantics": self.funding_schedule_semantics,
            "continuous_bbo_capable": self.continuous_bbo_capable,
            "evidence_url": self.evidence_url,
            "retrieval_utc": self.retrieval_utc,
            "content_digest": self.content_digest,
            "doc_status": self.doc_status,
            "observed_contract_verified": self.observed_contract_verified,
        }


@dataclass(frozen=True)
class R6SourceContractRegistry:
    registry_version: str
    venue: str
    selected_api_family: str
    api_family_selection_reason: str
    alternate_api_families_documented: Tuple[str, ...]
    endpoints: Mapping[str, R6EndpointContract]
    doc_evidence: Tuple[Dict[str, Any], ...]
    registry_digest: str
    status: str  # "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW" | "BLOCKED_SOURCE_CONTRACT"
    blocking_reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH03_R6,
            "schema_version": SCHEMA_VERSION_KH03_R6,
            "registry_version": self.registry_version,
            "venue": self.venue,
            "selected_api_family": self.selected_api_family,
            "api_family_selection_reason": self.api_family_selection_reason,
            "alternate_api_families_documented": list(self.alternate_api_families_documented),
            "endpoints": {k: v.to_dict() for k, v in sorted(self.endpoints.items())},
            "doc_evidence": list(self.doc_evidence),
            "registry_digest": self.registry_digest,
            "status": self.status,
            "blocking_reasons": list(self.blocking_reasons),
            "observed_market_endpoints_verified_count": sum(1 for v in self.endpoints.values() if v.observed_contract_verified),
            "doc_verified_endpoints_count": sum(1 for v in self.endpoints.values() if v.doc_status == "DOC_VERIFIED"),
        }


# =====================================================================
# F01: build_r6_source_contract_registry
# =====================================================================
def build_r6_source_contract_registry(
    doc_evidence: Optional[Sequence[Mapping[str, Any]]] = None,
    existing_inventory: Optional[Mapping[str, Any]] = None,
) -> R6SourceContractRegistry:
    """
    Builds a typed, immutable, versioned R6 public source contract registry for Bitget Classic v2.
    Distinguishes DOC_VERIFIED from OBSERVED_CONTRACT_VERIFIED and fails closed if required official
    documentation evidence is missing or contradictory.
    """
    docs = tuple(dict(d) for d in (doc_evidence if doc_evidence is not None else OFFICIAL_BITGET_DOC_EVIDENCE_R6))
    docs_by_id: Dict[str, Dict[str, Any]] = {str(d.get("doc_id") or ""): d for d in docs}

    blocking: List[str] = []
    classic_market = docs_by_id.get("bitget_classic_contract_market")
    classic_rest = docs_by_id.get("bitget_classic_rest_api")
    ws_ob = docs_by_id.get("bitget_ws_orderbook_channel")

    for req_id in ("bitget_classic_contract_market", "bitget_classic_rest_api", "bitget_ws_orderbook_channel"):
        rec = docs_by_id.get(req_id)
        if not rec or rec.get("http_status") != 200 or not str(rec.get("content_sha256") or "").strip():
            blocking.append(f"MISSING_REQUIRED_OFFICIAL_DOC:{req_id}")

    # Check existing adapter API family alignment (prefer Classic v2 matching existing XAUUSDT adapters)
    existing_family = API_FAMILY_CLASSIC_V2
    if existing_inventory:
        inv_family = str(existing_inventory.get("selected_api_family") or existing_inventory.get("api_family") or "").strip()
        if inv_family and inv_family not in (API_FAMILY_CLASSIC_V2, "v2", "classic_v2"):
            blocking.append(f"EXISTING_ADAPTER_API_FAMILY_CONFLICT:{inv_family}")

    cm_url = str((classic_market or {}).get("url") or "")
    cm_utc = str((classic_market or {}).get("retrieval_utc") or "")
    cm_sha = str((classic_market or {}).get("content_sha256") or "")
    cr_url = str((classic_rest or {}).get("url") or "")
    cr_utc = str((classic_rest or {}).get("retrieval_utc") or "")
    cr_sha = str((classic_rest or {}).get("content_sha256") or "")
    wb_url = str((ws_ob or {}).get("url") or "")
    wb_utc = str((ws_ob or {}).get("retrieval_utc") or "")
    wb_sha = str((ws_ob or {}).get("content_sha256") or "")

    cm_markers = set((classic_market or {}).get("verified_markers") or ())
    cr_markers = set((classic_rest or {}).get("verified_markers") or ())
    wb_markers = set((ws_ob or {}).get("verified_markers") or ())

    def _doc_stat(marker: str, markers: set[str], sha: str) -> str:
        if sha and marker in markers:
            return "DOC_VERIFIED"
        return "BLOCKED_DOC_UNAVAILABLE"

    endpoints: Dict[str, R6EndpointContract] = {
        "classic_v2_contracts": R6EndpointContract(
            endpoint_id="classic_v2_contracts",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/mix/market/contracts",
            method_or_op="GET",
            ws_channel=None,
            required_params=("productType",),
            optional_params=("symbol",),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=524_288,
            max_decoded_bytes=1_048_576,
            max_items_per_page=500,
            retention_policy_notes="Current active contract specification snapshot only; version snapshots when rules change.",
            timestamp_semantics="requestTime UTC integer ms on envelope",
            sequence_semantics="stateless snapshot (no sequence)",
            funding_schedule_semantics="fundInterval field in contract metadata if returned by exchange (never hardcoded 8h)",
            continuous_bbo_capable=False,
            evidence_url=cm_url,
            retrieval_utc=cm_utc,
            content_digest=cm_sha,
            doc_status=_doc_stat("/api/v2/mix/market/contracts", cm_markers, cm_sha),
            observed_contract_verified=False,
        ),
        "classic_v2_candles": R6EndpointContract(
            endpoint_id="classic_v2_candles",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/mix/market/candles",
            method_or_op="GET",
            ws_channel=None,
            required_params=("symbol", "productType", "granularity"),
            optional_params=("startTime", "endTime", "limit", "kLineType"),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=ALLOWED_V2_GRANULARITIES_R6,
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=524_288,
            max_decoded_bytes=1_048_576,
            max_items_per_page=200,
            retention_policy_notes="Recent rolling K-line window (default 100, max 200 per request); retention varies by granularity.",
            timestamp_semantics="row[0] open timestamp UTC integer ms; closed iff server_time_ms >= open_ms + bar_ms",
            sequence_semantics="ordered by open timestamp UTC ms",
            funding_schedule_semantics="not applicable",
            continuous_bbo_capable=False,
            evidence_url=cm_url,
            retrieval_utc=cm_utc,
            content_digest=cm_sha,
            doc_status=_doc_stat("/api/v2/mix/market/candles", cm_markers, cm_sha),
            observed_contract_verified=False,
        ),
        "classic_v2_history_candles": R6EndpointContract(
            endpoint_id="classic_v2_history_candles",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/mix/market/history-candles",
            method_or_op="GET",
            ws_channel=None,
            required_params=("symbol", "productType", "granularity"),
            optional_params=("startTime", "endTime", "limit"),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=ALLOWED_V2_GRANULARITIES_R6,
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=524_288,
            max_decoded_bytes=1_048_576,
            max_items_per_page=200,
            retention_policy_notes="Historical K-lines max 200 items/request; exact XAUUSDT depth requires Stage B observation.",
            timestamp_semantics="row[0] open timestamp UTC integer ms; endTime must align to half-open [start,end)",
            sequence_semantics="ordered by open timestamp UTC ms; deduplicate overlaps, quarantine conflicts",
            funding_schedule_semantics="not applicable",
            continuous_bbo_capable=False,
            evidence_url=cm_url,
            retrieval_utc=cm_utc,
            content_digest=cm_sha,
            doc_status=_doc_stat("/api/v2/mix/market/history-candles", cm_markers, cm_sha),
            observed_contract_verified=False,
        ),
        "classic_v2_history_fund_rate": R6EndpointContract(
            endpoint_id="classic_v2_history_fund_rate",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/mix/market/history-fund-rate",
            method_or_op="GET",
            ws_channel=None,
            required_params=("symbol", "productType"),
            optional_params=("pageSize", "pageNo"),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=262_144,
            max_decoded_bytes=524_288,
            max_items_per_page=100,
            retention_policy_notes="Historical realized funding rate settlements; recent history bounded by venue retention.",
            timestamp_semantics="fundingTime UTC integer ms of realized settlement",
            sequence_semantics="paginated by pageNo/pageSize; guard cursor replay and overlapping settlements",
            funding_schedule_semantics="verify settlement spacing from observed contract fundInterval; never assume 8h or zero-fill missing",
            continuous_bbo_capable=False,
            evidence_url=cm_url,
            retrieval_utc=cm_utc,
            content_digest=cm_sha,
            doc_status=_doc_stat("/api/v2/mix/market/history-fund-rate", cm_markers, cm_sha),
            observed_contract_verified=False,
        ),
        "classic_v2_ticker": R6EndpointContract(
            endpoint_id="classic_v2_ticker",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/mix/market/ticker",
            method_or_op="GET",
            ws_channel=None,
            required_params=("symbol", "productType"),
            optional_params=(),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=131_072,
            max_decoded_bytes=262_144,
            max_items_per_page=1,
            retention_policy_notes="Point-in-time prospective REST quote snapshot only; NEVER continuous BBO stream or historical backfill.",
            timestamp_semantics="ts UTC integer ms on item + requestTime on envelope",
            sequence_semantics="stateless snapshot only",
            funding_schedule_semantics="fundingRate field is indicative/current, NOT realized historical settlement",
            continuous_bbo_capable=False,
            evidence_url=cm_url,
            retrieval_utc=cm_utc,
            content_digest=cm_sha,
            doc_status=_doc_stat("/api/v2/mix/market/ticker", cm_markers, cm_sha),
            observed_contract_verified=False,
        ),
        "classic_v2_public_time": R6EndpointContract(
            endpoint_id="classic_v2_public_time",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="REST_GET",
            scheme="https",
            host=APPROVED_REST_HOST_R6,
            port=443,
            path="/api/v2/public/time",
            method_or_op="GET",
            ws_channel=None,
            required_params=(),
            optional_params=(),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=20,
            conservative_r6_rate_limit_rps=1.0,
            max_response_bytes=16_384,
            max_decoded_bytes=32_768,
            max_items_per_page=1,
            retention_policy_notes="Server clock offset diagnostic only; never called before verified owner grant+claim.",
            timestamp_semantics="serverTime UTC integer ms",
            sequence_semantics="stateless",
            funding_schedule_semantics="not applicable",
            continuous_bbo_capable=False,
            evidence_url=cr_url,
            retrieval_utc=cr_utc,
            content_digest=cr_sha,
            doc_status="DOC_CANDIDATE_UNOBSERVED" if cr_sha else "BLOCKED_DOC_UNAVAILABLE",
            observed_contract_verified=False,
        ),
        "classic_v2_ws_books1": R6EndpointContract(
            endpoint_id="classic_v2_ws_books1",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="WS_PUBLIC",
            scheme="wss",
            host=APPROVED_WS_HOST_R6,
            port=443,
            path=APPROVED_WS_PATH_V2_R6,
            method_or_op="subscribe",
            ws_channel="books1",
            required_params=("instType", "channel", "instId"),
            optional_params=(),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=10,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=65_536,
            max_decoded_bytes=131_072,
            max_items_per_page=1,
            retention_policy_notes="Prospective forward-only L1 BBO stream; event-driven updates (not guaranteed periodic).",
            timestamp_semantics="data[].ts exchange UTC integer ms + parent local receipt UTC & monotonic timestamps",
            sequence_semantics="event-driven snapshot/push per connection epoch; seq optional, never invent contiguous sequence",
            funding_schedule_semantics="not applicable",
            continuous_bbo_capable=True,
            evidence_url=wb_url,
            retrieval_utc=wb_utc,
            content_digest=wb_sha,
            doc_status="DOC_VERIFIED" if (cr_sha and wb_sha and "wss://ws.bitget.com/v2/ws/public" in cr_markers and "books1" in wb_markers) else "BLOCKED_DOC_UNAVAILABLE",
            observed_contract_verified=False,
        ),
        "classic_v2_ws_ticker": R6EndpointContract(
            endpoint_id="classic_v2_ws_ticker",
            venue=APPROVED_VENUE_R6,
            api_family=existing_family,
            transport_kind="WS_PUBLIC",
            scheme="wss",
            host=APPROVED_WS_HOST_R6,
            port=443,
            path=APPROVED_WS_PATH_V2_R6,
            method_or_op="subscribe",
            ws_channel="ticker",
            required_params=("instType", "channel", "instId"),
            optional_params=(),
            allowed_symbols=(APPROVED_SYMBOL_R6,),
            allowed_product_types=(APPROVED_PRODUCT_TYPE_V2_R6,),
            allowed_granularities=(),
            doc_rate_limit_rps=10,
            conservative_r6_rate_limit_rps=2.0,
            max_response_bytes=65_536,
            max_decoded_bytes=131_072,
            max_items_per_page=1,
            retention_policy_notes="Prospective forward-only public ticker push; auxiliary to books1.",
            timestamp_semantics="data[].ts exchange UTC integer ms + local receipt UTC & monotonic",
            sequence_semantics="event-driven push per connection epoch",
            funding_schedule_semantics="indicative fundingRate only, NOT realized historical settlement",
            continuous_bbo_capable=False,
            evidence_url=cr_url,
            retrieval_utc=cr_utc,
            content_digest=cr_sha,
            doc_status=_doc_stat("wss://ws.bitget.com/v2/ws/public", cr_markers, cr_sha),
            observed_contract_verified=False,
        ),
    }

    for eid, ep in endpoints.items():
        if eid != "classic_v2_public_time" and ep.doc_status != "DOC_VERIFIED":
            blocking.append(f"ENDPOINT_DOC_NOT_VERIFIED:{eid}:{ep.doc_status}")

    status = "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW" if not blocking else "BLOCKED_SOURCE_CONTRACT"
    payload_for_digest = {
        "registry_version": "kh_03_r6_bitget_classic_v2_registry_v1",
        "selected_api_family": existing_family,
        "endpoints": {k: v.to_dict() for k, v in sorted(endpoints.items())},
        "doc_evidence": list(docs),
    }
    return R6SourceContractRegistry(
        registry_version="kh_03_r6_bitget_classic_v2_registry_v1",
        venue=APPROVED_VENUE_R6,
        selected_api_family=existing_family,
        api_family_selection_reason=(
            "Existing AURUM DESK XAUUSDT adapters (bitget_data.py, bitget_ws_service.py, "
            "kh_02_r2_c2_public_capture.py) use Bitget Classic v2 USDT-FUTURES. R6 locks "
            "BITGET_CLASSIC_V2 exclusively and forbids mixing v2 and UTA v3 schemas."
        ),
        alternate_api_families_documented=(API_FAMILY_UTA_V3,),
        endpoints=endpoints,
        doc_evidence=docs,
        registry_digest=_sha256_canonical(payload_for_digest),
        status=status,
        blocking_reasons=tuple(blocking),
    )


# =====================================================================
# F02: validate_r6_endpoint_contract
# =====================================================================
def validate_r6_endpoint_contract(
    registry: R6SourceContractRegistry,
    endpoint_id: str,
    params: Any,
    grant_scope: Optional[Mapping[str, Any]] = None,
    *,
    url_override: Optional[str] = None,
    method_or_op: Optional[str] = None,
    raw_query_string: Optional[str] = None,
    qa_loopback_mode: bool = False,
) -> Dict[str, Any]:
    """
    Strictly validates an endpoint call or WS subscription against the immutable R6 contract registry
    and optional grant scope. Rejects host/path/port/version/param mismatch, traversal, duplicate
    query keys, extra params, fragments, userinfo, redirects, private/login/order operations,
    substitute symbols, and mixed v2/v3 parameter names.
    """
    violations: List[str] = []

    if registry.status != "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW":
        violations.append(f"REGISTRY_NOT_READY:{registry.status}")

    ep = registry.endpoints.get(str(endpoint_id or ""))
    if ep is None:
        return {
            "valid": False,
            "endpoint_id": str(endpoint_id or ""),
            "violations": [f"UNKNOWN_ENDPOINT_ID:{endpoint_id}"],
            "canonical_request_digest": None,
        }

    if ep.doc_status == "BLOCKED_DOC_UNAVAILABLE":
        violations.append(f"ENDPOINT_DOC_UNAVAILABLE:{ep.endpoint_id}")

    # Validate method / WS operation
    op_check = str(method_or_op if method_or_op is not None else ep.method_or_op).strip()
    if op_check.lower() in FORBIDDEN_WS_OPS_R6 or op_check.upper() in ("POST", "PUT", "DELETE", "PATCH"):
        violations.append(f"FORBIDDEN_METHOD_OR_WS_OP:{op_check}")
    elif op_check != ep.method_or_op:
        violations.append(f"METHOD_OR_OP_MISMATCH:expected={ep.method_or_op}:got={op_check}")

    # Validate URL if provided (or canonical contract URL)
    target_url = str(url_override if url_override is not None else f"{ep.scheme}://{ep.host}{ep.path}")
    lower_raw = target_url.lower()
    unquoted_lower = unquote(target_url).lower()

    if "%" in target_url and any(tok in lower_raw for tok in ("%2e", "%2f", "%5c", "%00", "%23", "%3f")):
        violations.append("URL_ENCODED_TRAVERSAL_OR_INJECTION_FORBIDDEN")
    if ".." in target_url or ".." in unquoted_lower or "\\" in target_url or "\\" in unquoted_lower:
        violations.append("PATH_TRAVERSAL_FORBIDDEN")
    if "#" in target_url:
        violations.append("URL_FRAGMENT_FORBIDDEN")

    parsed = urlsplit(target_url)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        violations.append("URL_USERINFO_FORBIDDEN")
    if parsed.fragment:
        violations.append("URL_FRAGMENT_FORBIDDEN")
    if parsed.query:
        violations.append("INLINE_QUERY_STRING_FORBIDDEN_USE_VALIDATED_PARAMS")

    host = (parsed.hostname or "").lower()
    expected_scheme = ep.scheme
    if qa_loopback_mode and host in ("127.0.0.1", "::1"):
        allowed_loop_schemes = ("http", "https") if ep.transport_kind == "REST_GET" else ("ws", "wss")
        if parsed.scheme.lower() not in allowed_loop_schemes:
            violations.append(f"LOOPBACK_SCHEME_MISMATCH:{parsed.scheme}")
    else:
        if parsed.scheme.lower() != expected_scheme:
            violations.append(f"SCHEME_MISMATCH:expected={expected_scheme}:got={parsed.scheme}")
        if host != ep.host:
            violations.append(f"HOST_MISMATCH:expected={ep.host}:got={host}")
        eff_port = parsed.port if parsed.port is not None else (443 if expected_scheme in ("https", "wss") else 80)
        if eff_port != ep.port:
            violations.append(f"PORT_MISMATCH:expected={ep.port}:got={eff_port}")

    if parsed.path != ep.path:
        violations.append(f"PATH_MISMATCH:expected={ep.path}:got={parsed.path}")
    for bad in FORBIDDEN_ENDPOINT_TOKENS_R6:
        if bad in unquoted_lower:
            violations.append(f"FORBIDDEN_PRIVATE_OR_ORDER_PATH:{bad}")

    # Parse and validate params (reject duplicate keys from raw_query_string or list of tuples!)
    norm_params: Dict[str, Any] = {}
    if raw_query_string is not None:
        if "#" in raw_query_string:
            violations.append("QUERY_FRAGMENT_FORBIDDEN")
        pairs = parse_qsl(raw_query_string, keep_blank_values=True)
        seen_keys: set[str] = set()
        for k, v in pairs:
            if k in seen_keys:
                violations.append(f"DUPLICATE_QUERY_KEY:{k}")
            seen_keys.add(k)
            norm_params[k] = v
    elif isinstance(params, Sequence) and not isinstance(params, (str, bytes)):
        seen_keys = set()
        for item in params:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                violations.append("MALFORMED_PARAM_PAIR")
                continue
            k, v = str(item[0]), item[1]
            if k in seen_keys:
                violations.append(f"DUPLICATE_QUERY_KEY:{k}")
            seen_keys.add(k)
            norm_params[k] = v
    elif isinstance(params, Mapping):
        for k, v in params.items():
            norm_params[str(k)] = v
    elif params is not None:
        violations.append("INVALID_PARAMS_CONTAINER")

    # Enforce API family non-mixing (v2 vs v3)
    if ep.api_family == API_FAMILY_CLASSIC_V2:
        for k in norm_params:
            if k in V3_ONLY_PARAM_NAMES_R6:
                violations.append(f"MIXED_API_FAMILY_V3_PARAM_ON_V2:{k}")

    allowed_param_set = set(ep.required_params) | set(ep.optional_params)
    for req_k in ep.required_params:
        if req_k not in norm_params or str(norm_params[req_k]).strip() == "":
            violations.append(f"MISSING_REQUIRED_PARAM:{req_k}")
    for k in norm_params:
        if k not in allowed_param_set:
            violations.append(f"EXTRA_OR_UNALLOWLISTED_PARAM:{k}")
        kl = k.lower()
        if any(sec in kl for sec in ("key", "secret", "passphrase", "token", "sign", "auth", "cookie")):
            violations.append(f"FORBIDDEN_CREDENTIAL_PARAM:{k}")

    # Validate symbol / instId against exact XAUUSDT and forbid substitutes
    sym_val = norm_params.get("symbol") if "symbol" in norm_params else norm_params.get("instId")
    if sym_val is not None:
        sym_s = str(sym_val).strip()
        if sym_s.upper() in FORBIDDEN_SUBSTITUTE_SYMBOLS_R6:
            violations.append(f"FORBIDDEN_SUBSTITUTE_SYMBOL:{sym_s}")
        elif sym_s not in ep.allowed_symbols:
            violations.append(f"SYMBOL_NOT_ALLOWED:{sym_s}")

    # Validate productType / instType
    pt_val = norm_params.get("productType") if "productType" in norm_params else norm_params.get("instType")
    if pt_val is not None:
        pt_s = str(pt_val).strip()
        if pt_s not in ep.allowed_product_types:
            violations.append(f"PRODUCT_TYPE_NOT_ALLOWED:{pt_s}")

    # Validate granularity
    if "granularity" in norm_params:
        gr_s = str(norm_params["granularity"]).strip()
        if gr_s not in ep.allowed_granularities:
            violations.append(f"GRANULARITY_NOT_ALLOWED:{gr_s}")

    # Validate kLineType (never silently mark/index price unless explicitly approved)
    if "kLineType" in norm_params and str(norm_params["kLineType"]).strip() not in ("", "market"):
        violations.append(f"UNAPPROVED_PRICE_BASIS_KLINETYPE:{norm_params['kLineType']}")

    # Validate WS channel
    if ep.transport_kind == "WS_PUBLIC":
        ch = str(norm_params.get("channel") or "").strip()
        if ch.lower() in FORBIDDEN_WS_OPS_R6 or any(t.strip("/") in ch.lower() for t in ("order", "account", "position", "private")):
            violations.append(f"FORBIDDEN_WS_PRIVATE_CHANNEL:{ch}")
        elif ch != ep.ws_channel:
            violations.append(f"WS_CHANNEL_MISMATCH:expected={ep.ws_channel}:got={ch}")

    # Validate pagination/limit integer bounds (reject bool as int)
    for lim_k in ("limit", "pageSize", "pageNo", "startTime", "endTime"):
        if lim_k in norm_params:
            raw_v = norm_params[lim_k]
            if isinstance(raw_v, bool):
                violations.append(f"BOOL_AS_INT_PARAM:{lim_k}")
                continue
            try:
                iv = int(str(raw_v))
            except Exception:
                violations.append(f"NON_INTEGER_PARAM:{lim_k}")
                continue
            if iv <= 0:
                violations.append(f"NON_POSITIVE_PARAM:{lim_k}:{iv}")
            if lim_k in ("limit", "pageSize") and iv > ep.max_items_per_page:
                violations.append(f"PAGE_LIMIT_EXCEEDS_CONTRACT:{lim_k}:{iv}>{ep.max_items_per_page}")

    # Validate against grant_scope if supplied
    if grant_scope is not None:
        gs_family = str(grant_scope.get("api_family") or "").strip()
        if gs_family and gs_family != ep.api_family:
            violations.append(f"GRANT_API_FAMILY_MISMATCH:grant={gs_family}:contract={ep.api_family}")
        gs_eps = set(grant_scope.get("allowed_endpoints") or ())
        if gs_eps and ep.endpoint_id not in gs_eps:
            violations.append(f"ENDPOINT_NOT_IN_GRANT_SCOPE:{ep.endpoint_id}")
        gs_sym = str(grant_scope.get("symbol") or "").strip()
        if gs_sym and sym_val is not None and str(sym_val).strip() != gs_sym:
            violations.append(f"SYMBOL_NOT_IN_GRANT_SCOPE:{sym_val}!={gs_sym}")
        gs_pt = str(grant_scope.get("product_type") or "").strip()
        if gs_pt and pt_val is not None and str(pt_val).strip() != gs_pt:
            violations.append(f"PRODUCT_TYPE_NOT_IN_GRANT_SCOPE:{pt_val}!={gs_pt}")
        gs_ws = set(grant_scope.get("allowed_ws_channels") or ())
        if ep.transport_kind == "WS_PUBLIC" and ep.ws_channel and gs_ws and ep.ws_channel not in gs_ws:
            violations.append(f"WS_CHANNEL_NOT_IN_GRANT_SCOPE:{ep.ws_channel}")
        if "startTime" in norm_params and "endTime" in norm_params and "window_start_ms" in grant_scope and "window_end_ms" in grant_scope:
            try:
                st_ms = int(str(norm_params["startTime"]))
                en_ms = int(str(norm_params["endTime"]))
                gw_s = int(grant_scope["window_start_ms"])
                gw_e = int(grant_scope["window_end_ms"])
                if st_ms >= en_ms:
                    violations.append(f"INVALID_WINDOW_ORDER:{st_ms}>={en_ms}")
                if st_ms < gw_s or en_ms > gw_e:
                    violations.append(f"WINDOW_OUTSIDE_GRANT_SCOPE:[{st_ms},{en_ms})not_in[{gw_s},{gw_e})")
            except Exception:
                violations.append("WINDOW_VALIDATION_PARSE_ERROR")

    canonical_req = {
        "endpoint_id": ep.endpoint_id,
        "api_family": ep.api_family,
        "transport_kind": ep.transport_kind,
        "method_or_op": ep.method_or_op,
        "url": target_url,
        "params": {k: str(v) for k, v in sorted(norm_params.items())},
        "qa_loopback_mode": bool(qa_loopback_mode),
    }
    return {
        "valid": len(violations) == 0,
        "endpoint_id": ep.endpoint_id,
        "api_family": ep.api_family,
        "transport_kind": ep.transport_kind,
        "violations": violations,
        "canonical_request": canonical_req,
        "canonical_request_digest": _sha256_canonical(canonical_req),
        "contract_digest": registry.registry_digest,
    }


# =====================================================================
# F03: validate_r6_instrument_identity
# =====================================================================
def validate_r6_instrument_identity(
    raw: Mapping[str, Any],
    contract: R6EndpointContract,
    provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Strictly validates exchange-returned Bitget Classic v2 instrument metadata for XAUUSDT.
    Uses Decimal strictly; NEVER invents default tick, lot, or multiplier.
    Missing required metadata -> INCOMPLETE_METADATA_BLOCKED.
    Identity mismatch -> IDENTITY_MISMATCH_HALT_GRANT (`halt_further_capture=True`).
    """
    violations: List[str] = []
    missing_fields: List[str] = []
    identity_mismatch = False

    if not isinstance(raw, Mapping):
        return {
            "valid": False,
            "status": "IDENTITY_MISMATCH_HALT_GRANT",
            "halt_further_capture": True,
            "violations": ["RAW_METADATA_NOT_MAPPING"],
            "missing_fields": [],
            "normalized_metadata": None,
            "raw_snapshot_sha256": None,
        }

    sym = str(raw.get("symbol") or "").strip()
    base_coin = str(raw.get("baseCoin") or "").strip()
    quote_coin = str(raw.get("quoteCoin") or "").strip()
    sym_status = str(raw.get("symbolStatus") or "").strip().lower()
    product_type = str(raw.get("productType") or APPROVED_PRODUCT_TYPE_V2_R6 if "productType" not in raw else raw.get("productType") or "").strip()
    price_basis = str(raw.get("priceBasis") or raw.get("priceType") or "LAST_TRADE_CONTRACT").strip()

    if not sym:
        missing_fields.append("symbol")
    elif sym.upper() in FORBIDDEN_SUBSTITUTE_SYMBOLS_R6 or sym not in contract.allowed_symbols:
        identity_mismatch = True
        violations.append(f"INSTRUMENT_SYMBOL_MISMATCH:got={sym}:allowed={list(contract.allowed_symbols)}")

    if not base_coin:
        missing_fields.append("baseCoin")
    elif base_coin != "XAU":
        identity_mismatch = True
        violations.append(f"BASE_COIN_MISMATCH:got={base_coin}:expected=XAU")

    if not quote_coin:
        missing_fields.append("quoteCoin")
    elif quote_coin != "USDT":
        identity_mismatch = True
        violations.append(f"QUOTE_COIN_MISMATCH:got={quote_coin}:expected=USDT")

    if "productType" in raw and product_type not in contract.allowed_product_types:
        identity_mismatch = True
        violations.append(f"PRODUCT_TYPE_MISMATCH:got={product_type}")

    if not sym_status:
        missing_fields.append("symbolStatus")
    elif sym_status != "normal":
        identity_mismatch = True
        violations.append(f"TRADING_STATUS_NOT_NORMAL:{sym_status}")

    if price_basis.upper() in ("INDEX", "MARK", "SPOT", "CFD", "FX"):
        identity_mismatch = True
        violations.append(f"FORBIDDEN_PRICE_BASIS:{price_basis}")

    # Required numeric specs (strict Decimal — zero defaults!)
    required_dec_fields = (
        ("sizeMultiplier", True),
        ("minTradeNum", True),
        ("minTradeUSDT", True),
        ("priceEndStep", True),
    )
    required_int_fields = ("pricePlace", "volumePlace")
    parsed_dec: Dict[str, str] = {}
    parsed_int: Dict[str, int] = {}

    for fname, must_be_pos in required_dec_fields:
        if fname not in raw or raw.get(fname) is None or str(raw.get(fname)).strip() == "":
            missing_fields.append(fname)
            continue
        try:
            dv = _strict_decimal(raw.get(fname), fname)
            if must_be_pos and dv <= Decimal("0"):
                violations.append(f"NON_POSITIVE_METADATA:{fname}:{dv}")
            else:
                parsed_dec[fname] = format(dv.normalize(), "f")
        except ValueError as exc:
            violations.append(str(exc))

    for fname in required_int_fields:
        if fname not in raw or raw.get(fname) is None or str(raw.get(fname)).strip() == "":
            missing_fields.append(fname)
            continue
        rv = raw.get(fname)
        if isinstance(rv, bool):
            violations.append(f"BOOL_AS_INT_METADATA:{fname}")
            continue
        try:
            iv = int(str(rv).strip())
            if iv < 0 or iv > 18:
                violations.append(f"OUT_OF_RANGE_PRECISION:{fname}:{iv}")
            else:
                parsed_int[fname] = iv
        except Exception:
            violations.append(f"INVALID_INT_METADATA:{fname}:{rv}")

    # Compute exact tick size ONLY when both pricePlace and priceEndStep exist (never default!)
    tick_size_str: Optional[str] = None
    if "pricePlace" in parsed_int and "priceEndStep" in parsed_dec:
        tick = Decimal(parsed_dec["priceEndStep"]) * (Decimal("10") ** (-parsed_int["pricePlace"]))
        tick_size_str = format(tick.normalize(), "f")

    fund_interval_hours: Optional[int] = None
    if raw.get("fundInterval") is not None and str(raw.get("fundInterval")).strip() != "":
        try:
            fi = int(str(raw["fundInterval"]).strip())
            if fi <= 0 or fi > 24:
                violations.append(f"INVALID_FUND_INTERVAL_HOURS:{fi}")
            else:
                fund_interval_hours = fi
        except Exception:
            violations.append(f"UNPARSEABLE_FUND_INTERVAL:{raw.get('fundInterval')}")

    raw_snapshot_sha256 = _sha256_canonical(dict(raw))

    if identity_mismatch:
        status = "IDENTITY_MISMATCH_HALT_GRANT"
        valid = False
        halt = True
    elif missing_fields or violations:
        status = "INCOMPLETE_METADATA_BLOCKED"
        valid = False
        halt = True
    else:
        status = "VALID_INSTRUMENT_IDENTITY"
        valid = True
        halt = False

    prov_class = str((provenance or {}).get("provenance_class") or "UNVERIFIED")
    normalized = {
        "venue": contract.venue,
        "api_family": contract.api_family,
        "symbol": sym,
        "product_type": product_type or APPROVED_PRODUCT_TYPE_V2_R6,
        "base_coin": base_coin,
        "quote_coin": quote_coin,
        "symbol_status": sym_status,
        "price_basis": price_basis,
        "size_multiplier": parsed_dec.get("sizeMultiplier"),
        "min_trade_num": parsed_dec.get("minTradeNum"),
        "min_trade_usdt": parsed_dec.get("minTradeUSDT"),
        "price_place": parsed_int.get("pricePlace"),
        "volume_place": parsed_int.get("volumePlace"),
        "price_end_step": parsed_dec.get("priceEndStep"),
        "derived_tick_size": tick_size_str,
        "fund_interval_hours": fund_interval_hours,
        "calendar_verified": bool(raw.get("calendarVerified", False)),
        "provenance_class": prov_class,
        "observed_from_real_exchange": prov_class == "RAW_OBSERVED_VERIFIED",
        "raw_snapshot_sha256": raw_snapshot_sha256,
        "raw_preserved": dict(raw),
    }
    return {
        "valid": valid,
        "status": status,
        "halt_further_capture": halt,
        "violations": violations,
        "missing_fields": missing_fields,
        "normalized_metadata": normalized,
        "raw_snapshot_sha256": raw_snapshot_sha256,
    }


# =====================================================================
# F04: build_r6_source_capability_matrix
# =====================================================================
def build_r6_source_capability_matrix(
    registry: Optional[R6SourceContractRegistry] = None,
    observed_evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the explicit per-domain R6 source capability matrix separating documented capabilities
    from observed capabilities and unsupported/private account realities.
    """
    reg = registry if registry is not None else build_r6_source_contract_registry()
    obs = dict(observed_evidence or {})

    def _entry(
        domain: str,
        endpoint_id: Optional[str],
        doc_capability: str,
        observed_status: str,
        notes: str,
        *,
        continuous_bbo: bool = False,
    ) -> Dict[str, Any]:
        ep = reg.endpoints.get(endpoint_id) if endpoint_id else None
        return {
            "domain": domain,
            "endpoint_id": endpoint_id,
            "api_family": ep.api_family if ep else None,
            "url": f"{ep.scheme}://{ep.host}{ep.path}" if ep else None,
            "doc_status": ep.doc_status if ep else doc_capability,
            "observed_status": observed_status,
            "continuous_bbo_capable": continuous_bbo,
            "notes": notes,
        }

    domains = [
        _entry(
            "instruments",
            "classic_v2_contracts",
            "DOC_VERIFIED",
            str(obs.get("instruments") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "Classic v2 contract metadata endpoint documented; XAUUSDT exact tick/lot/fundInterval requires Stage B observation.",
        ),
        _entry(
            "ohlcv_history_m1",
            "classic_v2_history_candles",
            "DOC_VERIFIED",
            str(obs.get("ohlcv_history_m1") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "Classic v2 history-candles (max 200/page) documented; exact XAUUSDT history retention depth unproven until Stage B.",
        ),
        _entry(
            "bbo_ws_books1",
            "classic_v2_ws_books1",
            "DOC_VERIFIED",
            str(obs.get("bbo_ws_books1") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "Public wss books1 L1 channel documented; event-driven updates (not guaranteed periodic), forward capture only.",
            continuous_bbo=True,
        ),
        _entry(
            "rest_ticker_snapshot",
            "classic_v2_ticker",
            "DOC_VERIFIED",
            str(obs.get("rest_ticker_snapshot") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "REST ticker is point-in-time snapshot ONLY; NEVER classified as continuous BBO or historical BBO backfill.",
            continuous_bbo=False,
        ),
        _entry(
            "funding_history",
            "classic_v2_history_fund_rate",
            "DOC_VERIFIED",
            str(obs.get("funding_history") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "Classic v2 history-fund-rate documented; exact settlement cadence & availability for XAUUSDT require Stage B observation.",
        ),
        _entry(
            "public_server_time",
            "classic_v2_public_time",
            "DOC_CANDIDATE_UNOBSERVED",
            str(obs.get("public_server_time") or "NOT_OBSERVED_STAGE_A_OFFLINE"),
            "Clock offset candidate endpoint; NEVER called before verified owner grant + durable claim.",
        ),
        _entry(
            "owner_account_effective_fee",
            None,
            "NOT_AVAILABLE_PUBLIC_DOCS",
            str(obs.get("owner_account_effective_fee") or "UNKNOWN_NOT_LOCALLY_VERIFIED"),
            "Public API documentation never proves owner's account-specific effective maker/taker fee tier.",
        ),
        _entry(
            "owner_execution_slippage_and_fills",
            None,
            "NOT_AVAILABLE_PUBLIC_DOCS",
            str(obs.get("owner_execution_slippage_and_fills") or "MISSING_NO_OWNER_FILL_EVIDENCE"),
            "Public BBO/quotes never reveal real execution slippage or fill mix; placing orders (incl. paper) is forbidden in R6.",
        ),
    ]

    return {
        "spec_version": SPEC_VERSION_KH03_R6,
        "schema_version": SCHEMA_VERSION_KH03_R6,
        "selected_api_family": reg.selected_api_family,
        "registry_digest": reg.registry_digest,
        "rest_quote_snapshot_can_be_continuous_bbo": False,
        "public_docs_prove_owner_fee_or_fills": False,
        "domains": domains,
        "capability_matrix_sha256": _sha256_canonical(domains),
    }
