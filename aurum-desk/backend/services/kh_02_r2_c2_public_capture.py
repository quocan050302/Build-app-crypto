"""
aurum-desk/backend/services/kh_02_r2_c2_public_capture.py

AURUM DESK — KH-02.R2.C2 Read-Only Public Bitget Prospective Market-Data Collector
SPEC VERSION: KH-02.R2.C2-v1.0 | STDLIB ONLY | NETWORK DISABLED BY DEFAULT

Closes the KH-02.R2.C1 P1 collector placeholder ("OFFLINE_READINESS_BUILD_NO_LIVE_HTTP_TRANSPORT_EXECUTED")
with a complete, mechanically verifiable read-only public transport implementation:
  - Strict GET-only allowlist of official Bitget v2/v3 public market endpoints (ZERO private/order/wallet endpoints).
  - Injectable HTTP transport (`PublicHttpTransport`) for full offline deterministic QA via fake transport.
  - Default `network_capture_enabled=False`, `persistent_capture_active=False`; refuses live network
    unless explicit separate operator authorization AND strict >= 20.5 GiB disk preflight BOTH pass.
  - Bounded rate-limiting, timeout, deterministic seeded backoff+jitter on 429/5xx, schema validation,
    closed-bar enforcement, crossed/stale BBO rejection, clock skew, gap/sleep-wake tracking,
    secret redaction/rejection, raw bytes SHA256, and canonical parsed SHA256.
  - Prospective L1 BBO is live-observation only (NEVER historical REST backfill); fee tier remains
    UNKNOWN_NOT_LOCALLY_VERIFIED; OOS_EVALUATIONS stays 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import random
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlencode
import urllib.request

from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    SCHEMA_VERSION_KH02_R2_C1,
    normalize_ny_local_to_utc_ms_r2_c1,
    scan_for_forbidden_secrets_r2_c1,
    validate_prospective_snapshot_contract_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2_C2 = "KH-02.R2.C2-v1.0"

# Official Bitget V2/V3 public read-only market endpoints verified against Bitget API documentation
OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2: Dict[str, Dict[str, Any]] = {
    "contracts": {
        "url": "https://api.bitget.com/api/v2/mix/market/contracts",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OHLCV_SNAPSHOT",
        "required_params": ("productType", "symbol"),
        "doc_verified_utc": "2026-11-22",
    },
    "public_ticker": {
        "url": "https://api.bitget.com/api/v2/mix/market/ticker",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OBSERVED_QUOTE",
        "required_params": ("productType", "symbol"),
        "historical_l1_bbo_available": False,
        "prospective_poll_snapshot_only": True,
        "doc_verified_utc": "2026-11-22",
    },
    "merge_depth": {
        "url": "https://api.bitget.com/api/v2/mix/market/merge-depth",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OBSERVED_QUOTE",
        "required_params": ("productType", "symbol"),
        "historical_l1_bbo_available": False,
        "prospective_poll_snapshot_only": True,
        "doc_verified_utc": "2026-11-22",
    },
    "orderbook_v3": {
        "url": "https://api.bitget.com/api/v3/market/orderbook",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OBSERVED_QUOTE",
        "required_params": ("category", "symbol"),
        "historical_l1_bbo_available": False,
        "prospective_poll_snapshot_only": True,
        "doc_verified_utc": "2026-11-22",
    },
    "candles": {
        "url": "https://api.bitget.com/api/v2/mix/market/candles",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OHLCV_SNAPSHOT",
        "required_params": ("productType", "symbol", "granularity"),
        "doc_verified_utc": "2026-11-22",
    },
    "history_candles": {
        "url": "https://api.bitget.com/api/v2/mix/market/history-candles",
        "method": "GET",
        "auth_required": False,
        "read_only": True,
        "data_origin": "OHLCV_SNAPSHOT",
        "required_params": ("productType", "symbol", "granularity"),
        "doc_verified_utc": "2026-11-22",
    },
}

FORBIDDEN_URL_TOKENS_C2: Tuple[str, ...] = (
    "/order/",
    "/orders",
    "/trade/",
    "/place-",
    "/cancel-",
    "/account",
    "/wallet",
    "/position",
    "/assets",
    "/transfer",
    "/withdraw",
    "/private",
    "/trade/",
)

GRANULARITY_TO_MS_C2: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1H": 3_600_000,
    "1h": 3_600_000,
}


@dataclass(frozen=True)
class RawHttpResponseC2:
    status_code: int
    body_bytes: bytes
    headers: Mapping[str, str]
    request_utc_ms: int
    response_utc_ms: int
    transport_kind: str = "INJECTED_FAKE_TRANSPORT"


class PublicHttpTransportProtocol(Protocol):
    def get(
        self,
        url: str,
        params: Mapping[str, Any],
        *,
        timeout_sec: float,
    ) -> RawHttpResponseC2:
        ...


@dataclass(frozen=True)
class OperatorCaptureAuthorizationC2:
    approval_id: str
    approved_by: str
    network_capture_enabled: bool = False
    persistent_capture_active: bool = False
    scope: str = "PUBLIC_READONLY_MARKET_DATA_ONLY"


class StdlibUrllibPublicTransportC2:
    """
    Production-ready stdlib GET-only public transport (ZERO extra dependencies, ZERO auth headers).
    ONLY callable when operator explicitly authorizes live network capture AND disk >= 20.5 GiB.
    """

    def get(
        self,
        url: str,
        params: Mapping[str, Any],
        *,
        timeout_sec: float,
    ) -> RawHttpResponseC2:
        qs = urlencode(sorted((str(k), str(v)) for k, v in params.items()))
        full_url = f"{url}?{qs}" if qs else url
        req = urllib.request.Request(
            full_url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "AurumDesk-KH02-R2-C2-ReadOnly/1.0"},
        )
        req_ms = int(time.time() * 1000)
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            body = resp.read()
            resp_ms = int(time.time() * 1000)
            hdrs = {str(k).lower(): str(v) for k, v in resp.headers.items()}
            return RawHttpResponseC2(
                status_code=int(resp.status),
                body_bytes=body,
                headers=hdrs,
                request_utc_ms=req_ms,
                response_utc_ms=resp_ms,
                transport_kind="STDLIB_URLLIB_HTTPS_GET",
            )


class C2PublicReadOnlyBitgetCollector:
    """
    KH-02.R2.C2 Read-Only Public Bitget XAUUSDT Prospective Collector:
      - Default: network_capture_enabled=False, persistent_capture_active=False.
      - Strict public GET endpoint allowlist + secret/cookie/account-ID scanner.
      - Deterministic seeded retry/backoff on 429 & 5xx with rate-limit cap.
      - Parses & validates L1 BBO (`OBSERVED_QUOTE`) and closed OHLCV candles (`OHLCV_SNAPSHOT`).
    """

    def __init__(
        self,
        *,
        authorization: Optional[OperatorCaptureAuthorizationC2] = None,
        transport: Optional[PublicHttpTransportProtocol] = None,
        timeout_sec: float = 10.0,
        max_retries: int = 3,
        base_backoff_sec: float = 0.05,
        max_backoff_sec: float = 2.0,
        backoff_seed: int = 20261122,
        min_request_interval_ms: int = 50,
        max_clock_skew_ms: int = 5_000,
        stale_quote_threshold_ms: int = 15_000,
        disconnect_gap_threshold_ms: int = 300_000,
        sleep_fn: Optional[Callable[[float], None]] = None,
        clock_ms_fn: Optional[Callable[[], int]] = None,
        is_fake_transport_qa: bool = False,
    ) -> None:
        self.authorization = authorization
        self.transport = transport
        self.timeout_sec = float(timeout_sec)
        self.max_retries = int(max_retries)
        self.base_backoff_sec = float(base_backoff_sec)
        self.max_backoff_sec = float(max_backoff_sec)
        self._rng = random.Random(int(backoff_seed))
        self.min_request_interval_ms = int(min_request_interval_ms)
        self.max_clock_skew_ms = int(max_clock_skew_ms)
        self.stale_quote_threshold_ms = int(stale_quote_threshold_ms)
        self.disconnect_gap_threshold_ms = int(disconnect_gap_threshold_ms)
        self._sleep = sleep_fn or (lambda _s: None)
        self._clock_ms = clock_ms_fn or (lambda: int(time.time() * 1000))
        self.is_fake_transport_qa = bool(is_fake_transport_qa)
        self._last_request_ms: Optional[int] = None
        self.session_gap_log: List[Dict[str, Any]] = []
        self.retry_audit_log: List[Dict[str, Any]] = []

    def describe_readiness(self) -> Dict[str, Any]:
        auth_ok = bool(
            self.authorization is not None
            and self.authorization.network_capture_enabled
            and bool(str(self.authorization.approval_id).strip())
        )
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "read_only": True,
            "order_or_private_endpoints_present": False,
            "api_key_or_secret_reads_present": False,
            "network_capture_enabled": auth_ok,
            "persistent_capture_active": bool(self.authorization and self.authorization.persistent_capture_active),
            "is_fake_transport_qa": self.is_fake_transport_qa,
            "official_bitget_public_allowlist": OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2,
            "historical_l1_bbo_available_from_rest": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "sampling_caveat": (
                "REST ticker/merge-depth/orderbook endpoints provide polled point-in-time top-of-book "
                "snapshots, NOT full tick-by-tick WebSocket orderbook completeness and NEVER historical L1 BBO backfill."
            ),
        }

    def _check_guards(
        self,
        endpoint_key: str,
        params: Mapping[str, Any],
        write_mount_probe: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if endpoint_key not in OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2:
            raise ValueError(f"FORBIDDEN_OR_UNKNOWN_PUBLIC_ENDPOINT:{endpoint_key}")
        spec = OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2[endpoint_key]
        url = str(spec["url"])
        url_l = url.lower()
        for bad_tok in FORBIDDEN_URL_TOKENS_C2:
            if bad_tok in url_l:
                raise PermissionError(f"FORBIDDEN_PRIVATE_OR_ORDER_ENDPOINT:{url}")

        if (
            self.authorization is None
            or not self.authorization.network_capture_enabled
            or not str(self.authorization.approval_id).strip()
        ):
            raise PermissionError(
                "PROSPECTIVE_NETWORK_CAPTURE_DISABLED_BY_DEFAULT: "
                "Explicit separate operator approval is required before public capture."
            )
        if self.authorization.scope != "PUBLIC_READONLY_MARKET_DATA_ONLY":
            raise PermissionError(f"INVALID_CAPTURE_SCOPE:{self.authorization.scope}")

        if not bool(write_mount_probe.get("can_run_heavy_stages", False)):
            raise RuntimeError(
                f"BLOCKED_LOW_DISK_CAPTURE_FORBIDDEN:status={write_mount_probe.get('status')},min_free_gib={write_mount_probe.get('min_free_gib')}"
            )

        secret_hits = scan_for_forbidden_secrets_r2_c1(dict(params))
        if secret_hits:
            raise PermissionError(f"FORBIDDEN_SECRETS_IN_QUERY_PARAMS:{','.join(secret_hits)}")

        for rp in spec["required_params"]:
            if rp not in params or not str(params.get(rp) or "").strip():
                raise ValueError(f"MISSING_REQUIRED_QUERY_PARAM:{endpoint_key}:{rp}")

        if self.transport is None:
            raise PermissionError("NO_HTTP_TRANSPORT_CONFIGURED_LIVE_NETWORK_DISABLED")
        return spec

    def _execute_get_with_retry(
        self,
        endpoint_key: str,
        url: str,
        params: Mapping[str, Any],
    ) -> RawHttpResponseC2:
        now_ms = self._clock_ms()
        if self._last_request_ms is not None:
            dt = now_ms - self._last_request_ms
            if dt > self.disconnect_gap_threshold_ms:
                self.session_gap_log.append(
                    {
                        "kind": "SESSION_DISCONNECT_OR_DEVICE_SLEEP_GAP",
                        "prev_ms": self._last_request_ms,
                        "curr_ms": now_ms,
                        "gap_ms": dt,
                    }
                )
            elif 0 <= dt < self.min_request_interval_ms:
                self._sleep((self.min_request_interval_ms - dt) / 1000.0)
        self._last_request_ms = now_ms

        last_err: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            try:
                assert self.transport is not None
                resp = self.transport.get(url, params, timeout_sec=self.timeout_sec)
            except Exception as exc:
                last_err = f"TRANSPORT_ERROR:{type(exc).__name__}:{exc}"
                self.retry_audit_log.append({"endpoint": endpoint_key, "attempt": attempt, "outcome": last_err})
                if attempt < self.max_retries:
                    delay = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** attempt) + self._rng.uniform(0.0, 0.02))
                    self._sleep(delay)
                    continue
                raise RuntimeError(f"HTTP_TRANSPORT_RETRY_EXHAUSTED:{last_err}") from exc

            # Check response headers for forbidden credential/cookie reflection
            hdr_secrets = scan_for_forbidden_secrets_r2_c1(dict(resp.headers))
            if hdr_secrets:
                raise PermissionError(f"FORBIDDEN_SECRET_OR_COOKIE_IN_RESPONSE_HEADERS:{','.join(hdr_secrets)}")

            sc = int(resp.status_code)
            if sc == 429 or (500 <= sc <= 599):
                last_err = f"HTTP_{sc}"
                retry_after = float(resp.headers.get("retry-after", 0.0) or 0.0)
                delay = min(
                    self.max_backoff_sec,
                    max(retry_after, self.base_backoff_sec * (2 ** attempt)) + self._rng.uniform(0.0, 0.02),
                )
                self.retry_audit_log.append({"endpoint": endpoint_key, "attempt": attempt, "status": sc, "backoff_sec": round(delay, 4)})
                if attempt < self.max_retries:
                    self._sleep(delay)
                    continue
                raise RuntimeError(f"HTTP_RETRY_EXHAUSTED:{last_err}")

            if sc != 200:
                raise RuntimeError(f"NON_200_HTTP_STATUS:{sc}")
            if not resp.body_bytes:
                raise ValueError("TRUNCATED_OR_EMPTY_HTTP_BODY")
            return resp

        raise RuntimeError(f"HTTP_RETRY_EXHAUSTED:{last_err}")

    def fetch_public_l1_bbo_snapshot(
        self,
        *,
        endpoint_key: str = "public_ticker",
        snapshot_id: str,
        write_mount_probe: Mapping[str, Any],
        symbol: str = "XAUUSDT",
        product_type: str = "USDT-FUTURES",
        freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
        storage_path: str = "escrow_oos_isolated/prosp_l1_bbo.blob",
    ) -> Dict[str, Any]:
        """
        Fetches and strictly validates a single prospective public L1 top-of-book snapshot
        (`public_ticker`, `merge_depth`, or `orderbook_v3`).
        """
        if endpoint_key not in ("public_ticker", "merge_depth", "orderbook_v3"):
            raise ValueError(f"INVALID_L1_BBO_ENDPOINT_KEY:{endpoint_key}")
        params: Dict[str, Any] = (
            {"category": product_type, "symbol": symbol, "limit": "5"}
            if endpoint_key == "orderbook_v3"
            else (
                {"productType": product_type, "symbol": symbol, "limit": "5"}
                if endpoint_key == "merge_depth"
                else {"productType": product_type, "symbol": symbol}
            )
        )
        spec = self._check_guards(endpoint_key, params, write_mount_probe)
        resp = self._execute_get_with_retry(endpoint_key, spec["url"], params)

        raw_sha = hashlib.sha256(resp.body_bytes).hexdigest()
        try:
            body = json.loads(resp.body_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"TRUNCATED_OR_MALFORMED_JSON_BODY:{type(exc).__name__}") from exc

        if not isinstance(body, dict) or str(body.get("code") or "") != "00000" or "data" not in body:
            raise ValueError(f"API_SCHEMA_DRIFT_OR_EXCHANGE_ERROR:code={body.get('code') if isinstance(body, dict) else 'NON_DICT'}")
        body_secrets = scan_for_forbidden_secrets_r2_c1(body)
        if body_secrets:
            raise PermissionError(f"FORBIDDEN_SECRETS_IN_RESPONSE_BODY:{','.join(body_secrets)}")

        data = body["data"]
        if endpoint_key == "public_ticker":
            item = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
            if not isinstance(item, dict) or "bidPr" not in item or "askPr" not in item or "ts" not in item:
                raise ValueError("API_SCHEMA_DRIFT_MISSING_TICKER_BBO_OR_TS")
            bid = float(item["bidPr"])
            ask = float(item["askPr"])
            event_ms = int(item["ts"])
        else:
            item = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else None)
            bids_arr = (item.get("bids") or item.get("b")) if isinstance(item, dict) else None
            asks_arr = (item.get("asks") or item.get("a")) if isinstance(item, dict) else None
            if not isinstance(item, dict) or not bids_arr or not asks_arr or "ts" not in item:
                raise ValueError("API_SCHEMA_DRIFT_MISSING_ORDERBOOK_BBO_OR_TS")
            bid = float(bids_arr[0][0])
            ask = float(asks_arr[0][0])
            event_ms = int(item["ts"])

        if event_ms <= 0:
            raise ValueError("MISSING_OR_INVALID_SERVER_TIMESTAMP")
        if bid <= 0.0 or ask <= 0.0 or bid >= ask:
            raise ValueError(f"CROSSED_OR_INVALID_BBO:bid={bid},ask={ask}")

        req_ms = int(resp.request_utc_ms)
        recv_ms = int(resp.response_utc_ms)
        skew_ms = recv_ms - event_ms
        if event_ms - recv_ms > self.max_clock_skew_ms:
            raise ValueError(f"FUTURE_CLOCK_SKEW_EXCEEDED:event={event_ms}>recv={recv_ms}+tol={self.max_clock_skew_ms}")
        if skew_ms > self.stale_quote_threshold_ms:
            raise ValueError(f"STALE_QUOTE_REJECTED:lag_ms={skew_ms}>max={self.stale_quote_threshold_ms}")
        if abs(skew_ms) > self.max_clock_skew_ms:
            raise ValueError(f"EXCESSIVE_CLOCK_SKEW_MS:{skew_ms}")

        # Single L1 quote covers [event_ms, event_ms + 1] closed observation boundary
        if event_ms < R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS:
            raise ValueError(
                f"EMBARGO_VIOLATION:event_ms={event_ms}<cutoff={R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}"
            )

        parsed_obj = {
            "symbol": symbol,
            "product_type": product_type,
            "endpoint_key": endpoint_key,
            "event_utc_ms": event_ms,
            "receive_utc_ms": recv_ms,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread_usd": round(ask - bid, 4),
            "transport_kind": resp.transport_kind,
            "prospective_poll_snapshot_only": True,
        }
        parsed_sha = canonical_hash(parsed_obj)
        dataset_sha = canonical_hash({"raw_sha": raw_sha, "parsed_sha": parsed_sha})
        known_ms = max(recv_ms, event_ms + 1)

        contract_entry: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "provider": "bitget_public_rest",
            "exchange": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange"],
            "product_type": product_type,
            "exchange_contract_id": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange_contract_id"],
            "symbol": symbol,
            "price_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["price_precision"],
            "tick_size": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["tick_size"],
            "qty_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_precision"],
            "qty_step": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_step"],
            "timeframe": "L1_SNAPSHOT",
            "endpoint": spec["url"],
            "request_utc_ms": req_ms,
            "response_utc_ms": recv_ms,
            "query_params": dict(params),
            "page_boundaries": [{"start_ms": event_ms, "end_ms": event_ms + 1}],
            "cursor": "DONE",
            "http_status": int(resp.status_code),
            "pagination_complete": True,
            "rate_limit_behavior": "BOUNDED_SEEDED_BACKOFF_OK",
            "license_or_access": "PUBLIC_MARKET_DATA_READONLY",
            "start_utc_ms": event_ms,
            "end_utc_ms": event_ms + 1,
            "event_or_close_utc_ms": event_ms,
            "capture_time_ms": known_ms,
            "known_at_ms": known_ms,
            "clock_skew_ms": skew_ms,
            "bar_count": 1,
            "utc_monotonic_strict": True,
            "data_origin": "OBSERVED_QUOTE",
            "observed_bid_ask_present": True,
            "quote_mode": "OBSERVED_L1_BBO",
            "raw_response_sha256": raw_sha,
            "canonical_parsed_sha256": parsed_sha,
            "pagination_lineage": ["page_1_of_1"],
            "source_signature_or_checksum": f"raw_sha256:{raw_sha[:16]}",
            "dataset_sha256": dataset_sha,
            "partition_role": "FRESH_UNTOUCHED",
            "dev_oos_physically_separated": True,
            "storage_path": storage_path,
            "account_fee_tier_status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "strategy_evaluation_count": 0,
        }
        val = validate_prospective_snapshot_contract_c1(
            contract_entry,
            freeze_timestamp_ms=freeze_timestamp_ms,
            known_dev_shas=known_dev_shas,
            max_clock_skew_ms=self.max_clock_skew_ms,
        )
        if not val["valid_for_escrow_admission"]:
            raise ValueError(f"SNAPSHOT_CONTRACT_VALIDATION_FAILED:{val['violations']}")

        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "is_fake_transport_qa": self.is_fake_transport_qa,
            "provenance_disclosure": (
                "FAKE_TRANSPORT_QA_ONLY_NOT_LIVE_MARKET_DATA"
                if self.is_fake_transport_qa
                else "PUBLIC_READONLY_POLL_SNAPSHOT"
            ),
            "raw_response_bytes": resp.body_bytes,
            "parsed_quote": parsed_obj,
            "snapshot_contract_entry": contract_entry,
            "validation": val,
            "session_gap_log": list(self.session_gap_log),
            "retry_audit_log": list(self.retry_audit_log),
        }

    def fetch_public_ohlcv_candles_snapshot(
        self,
        *,
        endpoint_key: str = "history_candles",
        snapshot_id: str,
        write_mount_probe: Mapping[str, Any],
        granularity: str = "1m",
        start_time_ms: int,
        end_time_ms: int,
        symbol: str = "XAUUSDT",
        product_type: str = "USDT-FUTURES",
        expected_min_bars: int = 1,
        freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
        known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
        storage_path: str = "escrow_oos_isolated/prosp_ohlcv.blob",
    ) -> Dict[str, Any]:
        """
        Fetches and strictly validates closed OHLCV candles (`candles` or `history_candles`),
        enforcing closed-bar invariants (close_time <= response_utc_ms), monotonic UTC,
        deduplication, pagination completeness, and R2 embargo cutoff.
        """
        if endpoint_key not in ("candles", "history_candles"):
            raise ValueError(f"INVALID_OHLCV_ENDPOINT_KEY:{endpoint_key}")
        if granularity not in GRANULARITY_TO_MS_C2:
            raise ValueError(f"UNSUPPORTED_GRANULARITY:{granularity}")
        tf_ms = GRANULARITY_TO_MS_C2[granularity]
        params = {
            "productType": product_type,
            "symbol": symbol,
            "granularity": granularity,
            "startTime": str(int(start_time_ms)),
            "endTime": str(int(end_time_ms)),
            "limit": "200",
        }
        spec = self._check_guards(endpoint_key, params, write_mount_probe)
        resp = self._execute_get_with_retry(endpoint_key, spec["url"], params)

        raw_sha = hashlib.sha256(resp.body_bytes).hexdigest()
        try:
            body = json.loads(resp.body_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"TRUNCATED_OR_MALFORMED_JSON_BODY:{type(exc).__name__}") from exc

        if not isinstance(body, dict) or str(body.get("code") or "") != "00000" or not isinstance(body.get("data"), list):
            raise ValueError("API_SCHEMA_DRIFT_OR_EXCHANGE_ERROR_CANDLES")
        body_secrets = scan_for_forbidden_secrets_r2_c1(body)
        if body_secrets:
            raise PermissionError(f"FORBIDDEN_SECRETS_IN_RESPONSE_BODY:{','.join(body_secrets)}")

        raw_rows = body["data"]
        if len(raw_rows) < int(expected_min_bars):
            raise ValueError(f"DROPPED_OR_INCOMPLETE_PAGE:got={len(raw_rows)}<expected_min={expected_min_bars}")
        if bool(body.get("hasMore", False)):
            raise ValueError("PARTIAL_UNFINISHED_PAGINATION_PAGE:hasMore=True")

        recv_ms = int(resp.response_utc_ms)
        req_ms = int(resp.request_utc_ms)
        parsed_bars: List[Dict[str, Any]] = []
        seen_open_ts: set[int] = set()
        prev_open_ts: Optional[int] = None
        gap_windows: List[Dict[str, int]] = []

        for idx, row in enumerate(raw_rows):
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError(f"MALFORMED_CANDLE_ROW:idx={idx}")
            o_ms = int(row[0])
            c_ms = o_ms + tf_ms
            o_px, h_px, l_px, c_px, vol = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
            if o_ms in seen_open_ts:
                raise ValueError(f"DUPLICATE_CANDLE_TIMESTAMP:idx={idx}:open_ms={o_ms}")
            seen_open_ts.add(o_ms)
            if prev_open_ts is not None:
                if o_ms <= prev_open_ts:
                    raise ValueError(f"OUT_OF_ORDER_OR_NON_MONOTONIC_CANDLE:idx={idx}:{o_ms}<={prev_open_ts}")
                if o_ms - prev_open_ts > tf_ms:
                    gap_windows.append({"from_ms": prev_open_ts + tf_ms, "to_ms": o_ms, "gap_ms": o_ms - prev_open_ts - tf_ms})
            prev_open_ts = o_ms

            if c_ms > recv_ms:
                raise ValueError(f"UNCLOSED_OR_FORMING_BAR_REJECTED:idx={idx}:close_ms={c_ms}>recv_ms={recv_ms}")
            if min(o_px, h_px, l_px, c_px) <= 0.0 or h_px < max(o_px, c_px) or l_px > min(o_px, c_px) or l_px > h_px or vol < 0.0:
                raise ValueError(f"CORRUPTED_OHLCV_INVARIANT:idx={idx}")

            parsed_bars.append(
                {
                    "open_time_ms": o_ms,
                    "close_time_ms": c_ms,
                    "open": round(o_px, 2),
                    "high": round(h_px, 2),
                    "low": round(l_px, 2),
                    "close": round(c_px, 2),
                    "volume": vol,
                    "is_closed": True,
                }
            )

        first_open = parsed_bars[0]["open_time_ms"]
        last_close = parsed_bars[-1]["close_time_ms"]
        if first_open < R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS:
            raise ValueError(f"EMBARGO_VIOLATION:start={first_open}<cutoff={R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}")

        parsed_sha = canonical_hash(parsed_bars)
        dataset_sha = canonical_hash({"raw_sha": raw_sha, "parsed_sha": parsed_sha})
        contract_entry: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "provider": "bitget_public_rest",
            "exchange": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange"],
            "product_type": product_type,
            "exchange_contract_id": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange_contract_id"],
            "symbol": symbol,
            "price_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["price_precision"],
            "tick_size": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["tick_size"],
            "qty_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_precision"],
            "qty_step": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_step"],
            "timeframe": granularity,
            "endpoint": spec["url"],
            "request_utc_ms": req_ms,
            "response_utc_ms": recv_ms,
            "query_params": dict(params),
            "page_boundaries": [{"start_ms": first_open, "end_ms": last_close}],
            "cursor": "DONE",
            "http_status": int(resp.status_code),
            "pagination_complete": True,
            "rate_limit_behavior": "BOUNDED_SEEDED_BACKOFF_OK",
            "license_or_access": "PUBLIC_MARKET_DATA_READONLY",
            "start_utc_ms": first_open,
            "end_utc_ms": last_close,
            "event_or_close_utc_ms": last_close,
            "capture_time_ms": recv_ms,
            "known_at_ms": recv_ms,
            "clock_skew_ms": 0,
            "bar_count": len(parsed_bars),
            "utc_monotonic_strict": True,
            "data_origin": "OHLCV_SNAPSHOT",
            "observed_bid_ask_present": False,
            "quote_mode": "NOT_AVAILABLE_OHLCV_ONLY",
            "raw_response_sha256": raw_sha,
            "canonical_parsed_sha256": parsed_sha,
            "pagination_lineage": ["page_1_of_1"],
            "source_signature_or_checksum": f"raw_sha256:{raw_sha[:16]}",
            "dataset_sha256": dataset_sha,
            "partition_role": "FRESH_UNTOUCHED",
            "dev_oos_physically_separated": True,
            "storage_path": storage_path,
            "account_fee_tier_status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "strategy_evaluation_count": 0,
        }
        val = validate_prospective_snapshot_contract_c1(
            contract_entry,
            freeze_timestamp_ms=freeze_timestamp_ms,
            known_dev_shas=known_dev_shas,
            max_clock_skew_ms=self.max_clock_skew_ms,
        )
        if not val["valid_for_escrow_admission"]:
            raise ValueError(f"SNAPSHOT_CONTRACT_VALIDATION_FAILED:{val['violations']}")

        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "is_fake_transport_qa": self.is_fake_transport_qa,
            "provenance_disclosure": (
                "FAKE_TRANSPORT_QA_ONLY_NOT_LIVE_MARKET_DATA"
                if self.is_fake_transport_qa
                else "PUBLIC_READONLY_OHLCV_SNAPSHOT"
            ),
            "raw_response_bytes": resp.body_bytes,
            "parsed_bars": parsed_bars,
            "gap_windows": gap_windows,
            "snapshot_contract_entry": contract_entry,
            "validation": val,
            "retry_audit_log": list(self.retry_audit_log),
        }


class _SequenceFakeTransportC2:
    """Deterministic fake transport returning scripted responses or raising scripted errors."""

    def __init__(self, script: Sequence[RawHttpResponseC2 | Exception]) -> None:
        self._items = list(script)
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        self.calls.append((url, dict(params)))
        nxt = self._items.pop(0) if len(self._items) > 1 else self._items[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def run_kh02_r2_c2_fake_transport_collector_suite() -> Dict[str, Any]:
    """
    §6.3: Runs ALL 20 mandatory fake-transport collector QA scenarios without making ANY
    live network calls. Explicitly labeled FAKE_TRANSPORT_QA_ONLY_NOT_LIVE_MARKET_DATA.
    """
    cutoff = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS
    ok_disk = {"can_run_heavy_stages": True, "status": "PASS_STRICT_AND_ADVISORY", "min_free_gib": 35.0}
    low_disk = {"can_run_heavy_stages": False, "status": "BLOCKED_LOW_DISK", "min_free_gib": 9.0}
    auth_ok = OperatorCaptureAuthorizationC2(
        approval_id="FAKE_TRANSPORT_QA_ONLY",
        approved_by="UNIT_TEST_HARNESS",
        network_capture_enabled=True,
    )

    def _mk_ticker_resp(ts_ms: int, bid: str = "4000.10", ask: str = "4000.30", *, req_ms: Optional[int] = None, recv_ms: Optional[int] = None, status: int = 200, extra: Optional[dict] = None, hdrs: Optional[dict] = None) -> RawHttpResponseC2:
        rq = req_ms if req_ms is not None else ts_ms + 10
        rs = recv_ms if recv_ms is not None else ts_ms + 30
        item = {"symbol": "XAUUSDT", "bidPr": bid, "askPr": ask, "ts": str(ts_ms)}
        if extra:
            item.update(extra)
        body = json.dumps({"code": "00000", "msg": "success", "data": [item]}).encode("utf-8")
        return RawHttpResponseC2(status_code=status, body_bytes=body, headers=hdrs or {}, request_utc_ms=rq, response_utc_ms=rs)

    def _mk_candles_resp(rows: list, *, recv_ms: int, has_more: bool = False, code: str = "00000") -> RawHttpResponseC2:
        payload: Dict[str, Any] = {"code": code, "msg": "success", "data": rows}
        if has_more:
            payload["hasMore"] = True
        return RawHttpResponseC2(
            status_code=200,
            body_bytes=json.dumps(payload).encode("utf-8"),
            headers={},
            request_utc_ms=recv_ms - 50,
            response_utc_ms=recv_ms,
        )

    t0 = cutoff + 60_000
    good_rows = [
        [str(t0), "4000.0", "4001.0", "3999.0", "4000.5", "10.0", "40005.0"],
        [str(t0 + 60_000), "4000.5", "4002.0", "4000.0", "4001.5", "12.0", "48018.0"],
    ]

    cases: List[Dict[str, Any]] = []

    def _rec(cid: str, name: str, passed: bool, detail: str) -> None:
        cases.append(
            {
                "case_id": cid,
                "name": name,
                "provenance": "FAKE_TRANSPORT_QA_ONLY_NOT_LIVE_MARKET_DATA",
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    # 1. Good L1 BBO ticker response
    c1 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0)]), is_fake_transport_qa=True)
    r1 = c1.fetch_public_l1_bbo_snapshot(snapshot_id="qa_bbo_01", write_mount_probe=ok_disk)
    _rec("COL-01", "GOOD_PUBLIC_L1_BBO_TICKER", r1["validation"]["valid_for_escrow_admission"] and r1["parsed_quote"]["spread_usd"] == 0.2, f"spread={r1['parsed_quote']['spread_usd']}")

    # 2. Good closed OHLCV candles response
    c2 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_candles_resp(good_rows, recv_ms=t0 + 180_000)]), is_fake_transport_qa=True)
    r2 = c2.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_ohlcv_01", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 120_000)
    _rec("COL-02", "GOOD_CLOSED_OHLCV_CANDLES", r2["validation"]["valid_for_escrow_admission"] and len(r2["parsed_bars"]) == 2, f"bars={len(r2['parsed_bars'])}")

    # 3. Stale quote rejected
    c3 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, recv_ms=t0 + 30_000)]), is_fake_transport_qa=True)
    ok3 = False
    try:
        c3.fetch_public_l1_bbo_snapshot(snapshot_id="qa_stale", write_mount_probe=ok_disk)
    except ValueError as e:
        ok3 = "STALE_QUOTE_REJECTED" in str(e)
    _rec("COL-03", "STALE_QUOTE_REJECTED", ok3, "30s stale quote blocked")

    # 4. HTTP 429 rate-limit retried cleanly then succeeds
    c4 = C2PublicReadOnlyBitgetCollector(
        authorization=auth_ok,
        transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, status=429, hdrs={"retry-after": "0.01"}), _mk_ticker_resp(t0)]),
        is_fake_transport_qa=True,
    )
    r4 = c4.fetch_public_l1_bbo_snapshot(snapshot_id="qa_429_ok", write_mount_probe=ok_disk)
    _rec("COL-04", "HTTP_429_BACKOFF_RETRY_SUCCEEDS", r4["validation"]["valid_for_escrow_admission"] and len(r4["retry_audit_log"]) == 1, f"retries={len(r4['retry_audit_log'])}")

    # 5. HTTP 5xx server error retried cleanly then succeeds (and exhausted raises RuntimeError)
    c5 = C2PublicReadOnlyBitgetCollector(
        authorization=auth_ok,
        transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, status=503), _mk_ticker_resp(t0)]),
        is_fake_transport_qa=True,
    )
    r5 = c5.fetch_public_l1_bbo_snapshot(snapshot_id="qa_5xx_ok", write_mount_probe=ok_disk)
    c5_ex = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, max_retries=1, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, status=503)]), is_fake_transport_qa=True)
    ok5_ex = False
    try:
        c5_ex.fetch_public_l1_bbo_snapshot(snapshot_id="qa_5xx_fail", write_mount_probe=ok_disk)
    except RuntimeError as e:
        ok5_ex = "HTTP_RETRY_EXHAUSTED" in str(e)
    _rec("COL-05", "HTTP_5XX_RETRY_AND_EXHAUSTION_HANDLING", r5["validation"]["valid_for_escrow_admission"] and ok5_ex, "503 retry + exhaustion verified")

    # 6. Dropped / incomplete pagination page rejected
    c6 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_candles_resp(good_rows, recv_ms=t0 + 180_000, has_more=True)]), is_fake_transport_qa=True)
    ok6 = False
    try:
        c6.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_partial_page", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 120_000)
    except ValueError as e:
        ok6 = "PARTIAL_UNFINISHED_PAGINATION_PAGE" in str(e)
    _rec("COL-06", "DROPPED_OR_PARTIAL_PAGE_REJECTED", ok6, "hasMore=True rejected")

    # 7. Missing timestamps rejected
    no_ts_resp = RawHttpResponseC2(200, json.dumps({"code": "00000", "data": [{"bidPr": "4000.1", "askPr": "4000.3"}]}).encode(), {}, t0, t0 + 20)
    c7 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([no_ts_resp]), is_fake_transport_qa=True)
    ok7 = False
    try:
        c7.fetch_public_l1_bbo_snapshot(snapshot_id="qa_no_ts", write_mount_probe=ok_disk)
    except ValueError as e:
        ok7 = "MISSING_TICKER_BBO_OR_TS" in str(e)
    _rec("COL-07", "MISSING_TIMESTAMPS_REJECTED", ok7, "missing ts rejected")

    # 8. Unclosed / forming bar rejected
    c8 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_candles_resp(good_rows, recv_ms=t0 + 90_000)]), is_fake_transport_qa=True)
    ok8 = False
    try:
        c8.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_unclosed", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 120_000)
    except ValueError as e:
        ok8 = "UNCLOSED_OR_FORMING_BAR_REJECTED" in str(e)
    _rec("COL-08", "UNCLOSED_FORMING_BAR_REJECTED", ok8, "forming bar close>recv blocked")

    # 9. Crossed book (bid >= ask) rejected
    c9 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, bid="4000.50", ask="4000.40")]), is_fake_transport_qa=True)
    ok9 = False
    try:
        c9.fetch_public_l1_bbo_snapshot(snapshot_id="qa_crossed", write_mount_probe=ok_disk)
    except ValueError as e:
        ok9 = "CROSSED_OR_INVALID_BBO" in str(e)
    _rec("COL-09", "CROSSED_BBO_BOOK_REJECTED", ok9, "bid >= ask rejected")

    # 10. Gap & device sleep/wake gap recorded explicitly
    gapped_rows = [good_rows[0], [str(t0 + 300_000), "4001.0", "4002.0", "4000.5", "4001.5", "5.0", "20007.5"]]
    clk_vals = iter([t0 + 400_000, t0 + 900_000])
    c10 = C2PublicReadOnlyBitgetCollector(
        authorization=auth_ok,
        transport=_SequenceFakeTransportC2([_mk_candles_resp(gapped_rows, recv_ms=t0 + 400_000), _mk_ticker_resp(t0 + 900_000)]),
        clock_ms_fn=lambda: next(clk_vals),
        is_fake_transport_qa=True,
    )
    r10a = c10.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_gap", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 360_000)
    r10b = c10.fetch_public_l1_bbo_snapshot(snapshot_id="qa_sleep_gap", write_mount_probe=ok_disk)
    _rec("COL-10", "BAR_GAP_AND_SLEEP_WAKE_DISCONNECT_LOGGED", len(r10a["gap_windows"]) == 1 and len(r10b["session_gap_log"]) == 1, f"bar_gaps={len(r10a['gap_windows'])}, sleep_gaps={len(r10b['session_gap_log'])}")

    # 11. Out-of-order bars rejected
    c11 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_candles_resp(list(reversed(good_rows)), recv_ms=t0 + 180_000)]), is_fake_transport_qa=True)
    ok11 = False
    try:
        c11.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_ooo", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 120_000)
    except ValueError as e:
        ok11 = "OUT_OF_ORDER_OR_NON_MONOTONIC" in str(e)
    _rec("COL-11", "OUT_OF_ORDER_BARS_REJECTED", ok11, "reversed candles rejected")

    # 12. Future server clock skew rejected
    c12 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0 + 20_000, req_ms=t0, recv_ms=t0 + 20)]), is_fake_transport_qa=True)
    ok12 = False
    try:
        c12.fetch_public_l1_bbo_snapshot(snapshot_id="qa_future_clock", write_mount_probe=ok_disk)
    except ValueError as e:
        ok12 = "FUTURE_CLOCK_SKEW_EXCEEDED" in str(e)
    _rec("COL-12", "FUTURE_SERVER_CLOCK_SKEW_REJECTED", ok12, "future ts rejected")

    # 13. DST New York local time disambiguation -> canonical UTC ms
    edt = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=0)
    est = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=1)
    _rec("COL-13", "DST_NEW_YORK_UTC_NORMALIZATION", est["utc_ms"] - edt["utc_ms"] == 3_600_000, f"edt={edt['utc_iso']}, est={est['utc_iso']}")

    # 14. Duplicate bar timestamp rejected
    dup_rows = [good_rows[0], good_rows[0]]
    c14 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_candles_resp(dup_rows, recv_ms=t0 + 180_000)]), is_fake_transport_qa=True)
    ok14 = False
    try:
        c14.fetch_public_ohlcv_candles_snapshot(snapshot_id="qa_dup", write_mount_probe=ok_disk, start_time_ms=t0, end_time_ms=t0 + 120_000)
    except ValueError as e:
        ok14 = "DUPLICATE_CANDLE_TIMESTAMP" in str(e)
    _rec("COL-14", "DUPLICATE_CANDLE_ID_REJECTED", ok14, "duplicate bar ts rejected")

    # 15. Secrets / cookie leakage in headers or body rejected
    c15 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0, hdrs={"set-cookie": "sid=LEAK"})]), is_fake_transport_qa=True)
    ok15 = False
    try:
        c15.fetch_public_l1_bbo_snapshot(snapshot_id="qa_secret", write_mount_probe=ok_disk)
    except PermissionError as e:
        ok15 = "FORBIDDEN_SECRET_OR_COOKIE" in str(e)
    _rec("COL-15", "SECRETS_AND_COOKIES_REJECTED", ok15, "set-cookie header rejected")

    # 16. Low disk blocks capture even when authorized
    c16 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0)]), is_fake_transport_qa=True)
    ok16 = False
    try:
        c16.fetch_public_l1_bbo_snapshot(snapshot_id="qa_low_disk", write_mount_probe=low_disk)
    except RuntimeError as e:
        ok16 = "BLOCKED_LOW_DISK_CAPTURE_FORBIDDEN" in str(e)
    _rec("COL-16", "LOW_DISK_BLOCKS_CAPTURE", ok16, "9.0 GiB blocked")

    # 17. Unauthorized default network disabled
    c17 = C2PublicReadOnlyBitgetCollector(transport=_SequenceFakeTransportC2([_mk_ticker_resp(t0)]), is_fake_transport_qa=True)
    ok17 = False
    try:
        c17.fetch_public_l1_bbo_snapshot(snapshot_id="qa_unauth", write_mount_probe=ok_disk)
    except PermissionError as e:
        ok17 = "PROSPECTIVE_NETWORK_CAPTURE_DISABLED_BY_DEFAULT" in str(e)
    _rec("COL-17", "DEFAULT_NETWORK_DISABLED", ok17 and c17.describe_readiness()["network_capture_enabled"] is False, "default OFF enforced")

    # 18. Network error handled fail-closed
    c18 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, max_retries=1, transport=_SequenceFakeTransportC2([ConnectionError("SIMULATED_NET_DOWN")]), is_fake_transport_qa=True)
    ok18 = False
    try:
        c18.fetch_public_l1_bbo_snapshot(snapshot_id="qa_net_err", write_mount_probe=ok_disk)
    except RuntimeError as e:
        ok18 = "HTTP_TRANSPORT_RETRY_EXHAUSTED" in str(e)
    _rec("COL-18", "NETWORK_ERROR_FAIL_CLOSED", ok18, "connection error fails closed")

    # 19. Truncated / malformed JSON body rejected
    trunc_resp = RawHttpResponseC2(200, b'{"code":"00000","data":[{"bidPr":"4000', {}, t0, t0 + 10)
    c19 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([trunc_resp]), is_fake_transport_qa=True)
    ok19 = False
    try:
        c19.fetch_public_l1_bbo_snapshot(snapshot_id="qa_trunc", write_mount_probe=ok_disk)
    except ValueError as e:
        ok19 = "TRUNCATED_OR_MALFORMED_JSON_BODY" in str(e)
    _rec("COL-19", "TRUNCATED_BODY_REJECTED", ok19, "truncated JSON rejected")

    # 20. API schema drift & forbidden private endpoint rejected
    drift_resp = RawHttpResponseC2(200, json.dumps({"code": "40001", "msg": "schema_changed"}).encode(), {}, t0, t0 + 10)
    c20 = C2PublicReadOnlyBitgetCollector(authorization=auth_ok, transport=_SequenceFakeTransportC2([drift_resp]), is_fake_transport_qa=True)
    ok20a, ok20b = False, False
    try:
        c20.fetch_public_l1_bbo_snapshot(snapshot_id="qa_drift", write_mount_probe=ok_disk)
    except ValueError as e:
        ok20a = "API_SCHEMA_DRIFT" in str(e)
    try:
        c20._check_guards("place_order_private", {}, ok_disk)
    except ValueError as e:
        ok20b = "FORBIDDEN_OR_UNKNOWN_PUBLIC_ENDPOINT" in str(e)
    _rec("COL-20", "API_SCHEMA_DRIFT_AND_FORBIDDEN_ENDPOINT_REJECTED", ok20a and ok20b, "drift & private endpoint blocked")

    passed = sum(1 for c in cases if c["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "provenance_disclosure": "ALL_20_COLLECTOR_TESTS_USE_INJECTED_FAKE_TRANSPORT_ZERO_LIVE_NETWORK_CALLS",
        "live_network_capture_executed": False,
        "real_prospective_snapshots_collected": 0,
        "total_cases": len(cases),
        "passed_cases": passed,
        "failed_cases": len(cases) - passed,
        "all_passed": passed == len(cases),
        "cases": cases,
        "status": "PASS" if passed == len(cases) else "FAIL",
    }
