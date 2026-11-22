"""
aurum-desk/backend/services/kh_02_r2_c3_provenance_guard.py

AURUM DESK — KH-02.R2.C3 Trusted Provenance Boundary & Raw-Byte Verification Firewall
SPEC VERSION: KH-02.R2.C3-v1.0 | STDLIB ONLY | ZERO NETWORK CALLS

Closes P0-A (Fake/Synthetic -> Real Prospective OOS Provenance Firewall):
  - Independent internal provenance classes (`ProvenanceClassC3`), sealed inside the
    trusted capture boundary with a process-scoped non-exported HMAC key (NEVER trusted
    from arbitrary client JSON flags such as `is_synthetic_fixture=False` or
    `partition_role="FRESH_UNTOUCHED"` alone).
  - Mandatory raw HTTP response bytes (`raw_payload_bytes`) for ANY real `OBSERVED_QUOTE`,
    `OBSERVED_TRADE`, or `OHLCV_SNAPSHOT` admitted as fresh OOS.
  - Independent raw-byte re-parsing & checksum verification (`reparse_and_verify_raw_market_bytes_c3`)
    re-deriving normalized quote/candle fields directly from raw bytes and verifying:
      * raw_response_sha256 == sha256(raw_payload_bytes)
      * canonical_parsed_sha256 matches freshly reparsed normalized data
      * provider event timestamps match claimed `event_or_close_utc_ms` / `start_utc_ms` / `end_utc_ms`
      * bounded byte size & gzip-bomb rejection
      * zero secrets/cookies/account IDs in decoded payload
      * embargo cutoff (`>= 2026-11-21T19:43:52Z`) & zero DEV overlap / canonical DEV SHA replay.
  - Honest Trusted Computing Base (TCB) disclosure: Python process controls provide strong
    defense-in-depth and fail-closed boundary isolation, NOT hardware TPM or remote WORM
    attestation against a privileged host root attacker.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
import secrets
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import zlib

from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    scan_for_forbidden_secrets_r2_c1,
    validate_prospective_snapshot_contract_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2_C3 = "KH-02.R2.C3-v1.0"
SCHEMA_VERSION_KH02_R2_C3_PROVENANCE = "kh_02_r2_c3_trusted_provenance_v1"

MAX_RAW_HTTP_BODY_BYTES_C3: int = 4 * 1024 * 1024  # 4 MiB hard cap
MAX_DECOMPRESSED_BODY_BYTES_C3: int = 8 * 1024 * 1024  # 8 MiB hard cap against gzip bombs

OFFICIAL_BITGET_PUBLIC_HOST_C3 = "api.bitget.com"
OFFICIAL_BITGET_PUBLIC_PATHS_C3: Tuple[str, ...] = (
    "/api/v2/mix/market/contracts",
    "/api/v2/mix/market/ticker",
    "/api/v2/mix/market/merge-depth",
    "/api/v3/market/orderbook",
    "/api/v2/mix/market/candles",
    "/api/v2/mix/market/history-candles",
)

GRANULARITY_TO_MS_C3: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
}

# Process-local ephemeral signing key — NEVER persisted, NEVER logged, NEVER serialized.
_PROCESS_RECEIPT_MAC_KEY_C3: bytes = secrets.token_bytes(32)

TCB_LIMITATIONS_DISCLOSURE_C3 = (
    "In-process HMAC capture receipts and raw-byte reparsing enforce a strict fail-closed "
    "provenance firewall between fake/synthetic QA fixtures and real authorized public HTTP "
    "capture within this codebase, preventing accidental or caller-flag crossing. "
    "HOWEVER, code executing with full local host/root privileges could modify local files "
    "or memory; this boundary does NOT provide hardware TPM attestation or hardware WORM."
)


class ProvenanceClassC3(str, Enum):
    QA_FAKE_TRANSPORT = "QA_FAKE_TRANSPORT"
    QA_SYNTHETIC_PAYLOAD = "QA_SYNTHETIC_PAYLOAD"
    LIVE_AUTHORIZED_PUBLIC_HTTP = "LIVE_AUTHORIZED_PUBLIC_HTTP"
    IMPORTED_HISTORICAL_OHLCV = "IMPORTED_HISTORICAL_OHLCV"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class TrustedCaptureReceiptC3:
    """
    Immutable capture receipt minted exclusively by `mint_trusted_capture_receipt_c3`.
    Binds raw HTTP response bytes hash, canonical parsed hash, host/TLS authority,
    endpoint, timestamps, and verified transport provenance class with an in-process MAC.
    Contains ZERO secrets, ZERO cookies, ZERO raw query credentials.
    """

    receipt_id: str
    capture_session_id: str
    provenance_class: ProvenanceClassC3
    endpoint_key: str
    url: str
    tls_host: str
    http_status: int
    params_canonical_sha256: str
    raw_response_sha256: str
    raw_response_size_bytes: int
    canonical_parsed_sha256: str
    request_utc_ms: int
    response_utc_ms: int
    monotonic_duration_ms: int
    provider_event_utc_ms: int
    transport_class_name: str
    transport_kind: str
    real_rate_limiter_active: bool
    receipt_mac_hex: str

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3_PROVENANCE,
            "receipt_id": self.receipt_id,
            "capture_session_id": self.capture_session_id,
            "provenance_class": self.provenance_class.value,
            "endpoint_key": self.endpoint_key,
            "url": self.url,
            "tls_host": self.tls_host,
            "http_status": self.http_status,
            "params_canonical_sha256": self.params_canonical_sha256,
            "raw_response_sha256": self.raw_response_sha256,
            "raw_response_size_bytes": self.raw_response_size_bytes,
            "canonical_parsed_sha256": self.canonical_parsed_sha256,
            "request_utc_ms": self.request_utc_ms,
            "response_utc_ms": self.response_utc_ms,
            "monotonic_duration_ms": self.monotonic_duration_ms,
            "provider_event_utc_ms": self.provider_event_utc_ms,
            "transport_class_name": self.transport_class_name,
            "transport_kind": self.transport_kind,
            "real_rate_limiter_active": self.real_rate_limiter_active,
            "receipt_mac_sha256_prefix": self.receipt_mac_hex[:16],
        }


def _compute_receipt_mac_c3(fields: Mapping[str, Any]) -> str:
    msg = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_PROCESS_RECEIPT_MAC_KEY_C3, msg, hashlib.sha256).hexdigest()


def _receipt_mac_fields(receipt: TrustedCaptureReceiptC3) -> Dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "capture_session_id": receipt.capture_session_id,
        "provenance_class": receipt.provenance_class.value,
        "endpoint_key": receipt.endpoint_key,
        "url": receipt.url,
        "tls_host": receipt.tls_host,
        "http_status": int(receipt.http_status),
        "params_canonical_sha256": receipt.params_canonical_sha256,
        "raw_response_sha256": receipt.raw_response_sha256,
        "raw_response_size_bytes": int(receipt.raw_response_size_bytes),
        "canonical_parsed_sha256": receipt.canonical_parsed_sha256,
        "request_utc_ms": int(receipt.request_utc_ms),
        "response_utc_ms": int(receipt.response_utc_ms),
        "monotonic_duration_ms": int(receipt.monotonic_duration_ms),
        "provider_event_utc_ms": int(receipt.provider_event_utc_ms),
        "transport_class_name": receipt.transport_class_name,
        "transport_kind": receipt.transport_kind,
        "real_rate_limiter_active": bool(receipt.real_rate_limiter_active),
    }


def verify_trusted_capture_receipt_mac_c3(receipt: Any) -> Tuple[bool, List[str]]:
    """Verifies that a TrustedCaptureReceiptC3 was minted inside this trusted process and unmodified."""
    if not isinstance(receipt, TrustedCaptureReceiptC3):
        return False, ["RECEIPT_NOT_TRUSTED_CAPTURE_RECEIPT_INSTANCE"]
    if not isinstance(receipt.provenance_class, ProvenanceClassC3):
        return False, ["INVALID_PROVENANCE_CLASS_TYPE"]
    expected = _compute_receipt_mac_c3(_receipt_mac_fields(receipt))
    if not hmac.compare_digest(str(receipt.receipt_mac_hex), expected):
        return False, ["FORGED_OR_TAMPERED_CAPTURE_RECEIPT_MAC"]
    return True, []


def safe_bounded_decompress_if_gzip_c3(
    raw_bytes: bytes,
    *,
    max_raw_bytes: int = MAX_RAW_HTTP_BODY_BYTES_C3,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BODY_BYTES_C3,
) -> bytes:
    """
    Bounded raw-byte and gzip-bomb protection. If payload begins with gzip magic bytes
    (`1f 8b`), decompresses incrementally with hard output limit `max_decompressed_bytes`.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise ValueError("EMPTY_OR_NON_BYTES_RAW_PAYLOAD")
    if len(raw_bytes) > int(max_raw_bytes):
        raise ValueError(f"OVERSIZED_RAW_PAYLOAD_BYTES:len={len(raw_bytes)}>max={max_raw_bytes}")
    b = bytes(raw_bytes)
    if len(b) >= 2 and b[0] == 0x1F and b[1] == 0x8B:
        try:
            decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
            out = decomp.decompress(b, int(max_decompressed_bytes) + 1)
            if len(out) > int(max_decompressed_bytes) or len(decomp.unconsumed_tail) > 0:
                raise ValueError(
                    f"GZIP_BOMB_OR_OVERSIZED_DECOMPRESSED_BODY:limit={max_decompressed_bytes}"
                )
            tail = decomp.flush(max(1, int(max_decompressed_bytes) + 1 - len(out)))
            out += tail
            if len(out) > int(max_decompressed_bytes):
                raise ValueError(
                    f"GZIP_BOMB_OR_OVERSIZED_DECOMPRESSED_BODY:limit={max_decompressed_bytes}"
                )
            return out
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"CORRUPT_GZIP_RAW_PAYLOAD:{type(exc).__name__}") from exc
    return b


def mint_trusted_capture_receipt_c3(
    *,
    capture_session_id: str,
    endpoint_key: str,
    url: str,
    params: Mapping[str, Any],
    http_status: int,
    raw_response_bytes: bytes,
    canonical_parsed_sha256: str,
    request_utc_ms: int,
    response_utc_ms: int,
    monotonic_duration_ms: int,
    provider_event_utc_ms: int,
    transport_obj: Any,
    transport_kind: str,
    tls_host: str,
    is_fake_transport_qa: bool,
    live_transport_verified: bool,
    live_authorization_verified: bool,
    real_rate_limiter_active: bool,
) -> TrustedCaptureReceiptC3:
    """
    Constructs and MAC-seals a TrustedCaptureReceiptC3.
    CRITICAL FIREWALL INVARIANT (P0-A):
      `LIVE_AUTHORIZED_PUBLIC_HTTP` is granted ONLY when ALL of the following hold simultaneously:
        1. `not is_fake_transport_qa`
        2. `live_transport_verified is True` (exact StdlibUrllibPublicTransportC2 with real socket TLS)
        3. `live_authorization_verified is True` (explicit OperatorCaptureAuthorizationC2 with live session)
        4. `transport_kind == "STDLIB_URLLIB_HTTPS_GET"`
        5. `tls_host == OFFICIAL_BITGET_PUBLIC_HOST_C3`
        6. `real_rate_limiter_active is True`
      Otherwise, provenance is FORCED to `QA_FAKE_TRANSPORT` (or `UNVERIFIED`) — even if the
      caller passed `is_fake_transport_qa=False` and believable live metadata!
    """
    if not isinstance(raw_response_bytes, (bytes, bytearray)) or len(raw_response_bytes) == 0:
        raise ValueError("CANNOT_MINT_RECEIPT_WITHOUT_RAW_BYTES")
    raw_sha = hashlib.sha256(raw_response_bytes).hexdigest()
    params_sha = canonical_hash(
        {str(k): str(v) for k, v in sorted(params.items(), key=lambda kv: str(kv[0]))}
    )

    qualifies_live = (
        (not bool(is_fake_transport_qa))
        and bool(live_transport_verified)
        and bool(live_authorization_verified)
        and str(transport_kind) == "STDLIB_URLLIB_HTTPS_GET"
        and str(tls_host).lower() == OFFICIAL_BITGET_PUBLIC_HOST_C3
        and bool(real_rate_limiter_active)
        and int(http_status) == 200
    )
    if qualifies_live:
        pclass = ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP
    elif bool(is_fake_transport_qa) or str(transport_kind) in (
        "INJECTED_FAKE_TRANSPORT",
        "TEST_INJECTED_OPENER_NOT_LIVE_HTTP",
    ):
        pclass = ProvenanceClassC3.QA_FAKE_TRANSPORT
    else:
        pclass = ProvenanceClassC3.UNVERIFIED

    rid_seed = canonical_hash(
        {
            "session": str(capture_session_id),
            "endpoint": str(endpoint_key),
            "raw_sha": raw_sha,
            "parsed_sha": str(canonical_parsed_sha256),
            "req_ms": int(request_utc_ms),
            "resp_ms": int(response_utc_ms),
        }
    )
    receipt_id = f"rcpt_c3_{rid_seed[:24]}"
    fields = {
        "receipt_id": receipt_id,
        "capture_session_id": str(capture_session_id or "UNASSIGNED_SESSION"),
        "provenance_class": pclass.value,
        "endpoint_key": str(endpoint_key),
        "url": str(url),
        "tls_host": str(tls_host or ""),
        "http_status": int(http_status),
        "params_canonical_sha256": params_sha,
        "raw_response_sha256": raw_sha,
        "raw_response_size_bytes": len(raw_response_bytes),
        "canonical_parsed_sha256": str(canonical_parsed_sha256).lower(),
        "request_utc_ms": int(request_utc_ms),
        "response_utc_ms": int(response_utc_ms),
        "monotonic_duration_ms": max(0, int(monotonic_duration_ms)),
        "provider_event_utc_ms": int(provider_event_utc_ms),
        "transport_class_name": type(transport_obj).__name__ if transport_obj is not None else "None",
        "transport_kind": str(transport_kind),
        "real_rate_limiter_active": bool(real_rate_limiter_active),
    }
    mac = _compute_receipt_mac_c3(fields)
    return TrustedCaptureReceiptC3(
        receipt_id=receipt_id,
        capture_session_id=fields["capture_session_id"],
        provenance_class=pclass,
        endpoint_key=fields["endpoint_key"],
        url=fields["url"],
        tls_host=fields["tls_host"],
        http_status=fields["http_status"],
        params_canonical_sha256=params_sha,
        raw_response_sha256=raw_sha,
        raw_response_size_bytes=fields["raw_response_size_bytes"],
        canonical_parsed_sha256=fields["canonical_parsed_sha256"],
        request_utc_ms=fields["request_utc_ms"],
        response_utc_ms=fields["response_utc_ms"],
        monotonic_duration_ms=fields["monotonic_duration_ms"],
        provider_event_utc_ms=fields["provider_event_utc_ms"],
        transport_class_name=fields["transport_class_name"],
        transport_kind=fields["transport_kind"],
        real_rate_limiter_active=fields["real_rate_limiter_active"],
        receipt_mac_hex=mac,
    )


def _infer_endpoint_key_from_url(url: str) -> Optional[str]:
    u = str(url or "").strip().lower()
    if u.endswith("/api/v2/mix/market/ticker"):
        return "public_ticker"
    if u.endswith("/api/v2/mix/market/merge-depth"):
        return "merge_depth"
    if u.endswith("/api/v3/market/orderbook"):
        return "orderbook_v3"
    if u.endswith("/api/v2/mix/market/candles"):
        return "candles"
    if u.endswith("/api/v2/mix/market/history-candles"):
        return "history_candles"
    if u.endswith("/api/v2/mix/market/contracts"):
        return "contracts"
    return None


def _is_valid_tick_price(px: float, tick_size: float = 0.01) -> bool:
    if px <= 0.0:
        return False
    scaled = px / tick_size
    return abs(scaled - round(scaled)) <= 1e-4


def reparse_and_verify_raw_market_bytes_c3(
    raw_payload_bytes: Optional[bytes],
    entry: Mapping[str, Any],
    *,
    capture_receipt: Optional[TrustedCaptureReceiptC3] = None,
    expected_transport_kind: Optional[str] = None,
    freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
) -> Dict[str, Any]:
    """
    Independently re-parses `raw_payload_bytes` from scratch and verifies byte-for-byte
    and field-for-field consistency against `entry` and optional `capture_receipt`:
      - Rejects None/empty/oversized/gzip-bomb raw bytes.
      - Recomputes `sha256(raw_payload_bytes)` vs `entry["raw_response_sha256"]`.
      - Scans decoded JSON for forbidden secrets/cookies/account IDs.
      - Re-derives normalized BBO quote (`OBSERVED_QUOTE`) or closed OHLCV bars (`OHLCV_SNAPSHOT`)
        directly from raw HTTP JSON and verifies:
          * `canonical_parsed_sha256` exact match
          * `dataset_sha256` exact match (when derived from raw+parsed)
          * provider event timestamp exact match against `entry["event_or_close_utc_ms"]`
          * closed-bar invariant, monotonic UTC, non-crossed BBO, tick precision, symbol match
          * post-embargo cutoff (`>= R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS`) and non-DEV overlap.
    """
    violations: List[str] = []
    if raw_payload_bytes is None:
        return {
            "valid": False,
            "raw_sha256": None,
            "recomputed_parsed_sha256": None,
            "violations": ["RAW_PAYLOAD_BYTES_REQUIRED_FOR_OOS_ADMISSION"],
        }

    try:
        decoded_bytes = safe_bounded_decompress_if_gzip_c3(raw_payload_bytes)
    except ValueError as exc:
        return {
            "valid": False,
            "raw_sha256": hashlib.sha256(raw_payload_bytes).hexdigest() if isinstance(raw_payload_bytes, (bytes, bytearray)) else None,
            "recomputed_parsed_sha256": None,
            "violations": [str(exc)],
        }

    actual_raw_sha = hashlib.sha256(raw_payload_bytes).hexdigest()
    claimed_raw_sha = str(entry.get("raw_response_sha256") or "").lower()
    if actual_raw_sha != claimed_raw_sha:
        violations.append(
            f"RAW_RESPONSE_SHA256_MISMATCH:claimed={claimed_raw_sha},actual={actual_raw_sha}"
        )
    if actual_raw_sha in {s.lower() for s in known_dev_shas}:
        violations.append("RAW_BYTES_MATCH_KNOWN_DEV_DATASET_FORBIDDEN")

    try:
        body = json.loads(decoded_bytes.decode("utf-8"))
    except Exception as exc:
        violations.append(f"RAW_BYTES_MALFORMED_JSON:{type(exc).__name__}")
        return {
            "valid": False,
            "raw_sha256": actual_raw_sha,
            "recomputed_parsed_sha256": None,
            "violations": violations,
        }

    if not isinstance(body, dict) or str(body.get("code") or "") != "00000" or "data" not in body:
        violations.append("RAW_BYTES_INVALID_BITGET_ENVELOPE")
        return {
            "valid": False,
            "raw_sha256": actual_raw_sha,
            "recomputed_parsed_sha256": None,
            "violations": violations,
        }

    body_secrets = scan_for_forbidden_secrets_r2_c1(body)
    if body_secrets:
        violations.append(f"FORBIDDEN_SECRETS_IN_RAW_PAYLOAD:{','.join(body_secrets)}")

    origin = str(entry.get("data_origin") or "")
    ep_key = _infer_endpoint_key_from_url(str(entry.get("endpoint") or ""))
    if ep_key is None:
        violations.append(f"UNALLOWLISTED_OR_UNKNOWN_ENDPOINT_URL:{entry.get('endpoint')}")
        return {
            "valid": False,
            "raw_sha256": actual_raw_sha,
            "recomputed_parsed_sha256": None,
            "violations": violations,
        }

    symbol = str(entry.get("symbol") or "")
    product_type = str(entry.get("product_type") or "")
    if symbol != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["symbol"]:
        violations.append(f"SYMBOL_MISMATCH:{symbol}")
    if product_type != BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["product_type"]:
        violations.append(f"PRODUCT_TYPE_MISMATCH:{product_type}")

    recv_ms = int(entry.get("response_utc_ms") or 0)
    recomputed_parsed_sha: Optional[str] = None
    recomputed_event_ms: Optional[int] = None
    data = body["data"]

    if origin == "OBSERVED_QUOTE":
        if ep_key not in ("public_ticker", "merge_depth", "orderbook_v3"):
            violations.append(f"ENDPOINT_ORIGIN_MISMATCH:origin={origin},endpoint={ep_key}")
        else:
            if ep_key == "public_ticker":
                item = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
                if not isinstance(item, dict) or "bidPr" not in item or "askPr" not in item or "ts" not in item:
                    violations.append("RAW_BYTES_MISSING_TICKER_BBO_OR_TS")
                    item = None
                else:
                    if item.get("symbol") and str(item["symbol"]) != symbol:
                        violations.append(f"RAW_TICKER_SYMBOL_MISMATCH:{item.get('symbol')}!={symbol}")
                    bid = float(item["bidPr"])
                    ask = float(item["askPr"])
                    recomputed_event_ms = int(item["ts"])
            else:
                item = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else None)
                bids_arr = (item.get("bids") or item.get("b")) if isinstance(item, dict) else None
                asks_arr = (item.get("asks") or item.get("a")) if isinstance(item, dict) else None
                if not isinstance(item, dict) or not bids_arr or not asks_arr or "ts" not in item:
                    violations.append("RAW_BYTES_MISSING_ORDERBOOK_BBO_OR_TS")
                    item = None
                else:
                    bid = float(bids_arr[0][0])
                    ask = float(asks_arr[0][0])
                    recomputed_event_ms = int(item["ts"])

            if item is not None and recomputed_event_ms is not None:
                if bid <= 0.0 or ask <= 0.0 or bid >= ask:
                    violations.append(f"RAW_BYTES_CROSSED_OR_INVALID_BBO:bid={bid},ask={ask}")
                if not _is_valid_tick_price(bid) or not _is_valid_tick_price(ask):
                    violations.append(f"RAW_BYTES_TICK_PRECISION_VIOLATION:bid={bid},ask={ask}")
                if recomputed_event_ms < R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS:
                    violations.append(
                        f"RAW_BYTES_EMBARGO_VIOLATION:event_ms={recomputed_event_ms}<cutoff={R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}"
                    )
                if recomputed_event_ms != int(entry.get("event_or_close_utc_ms") or -1):
                    violations.append(
                        f"EVENT_TIMESTAMP_TAMPERED_VS_RAW_BYTES:claimed={entry.get('event_or_close_utc_ms')},raw={recomputed_event_ms}"
                    )
                if recomputed_event_ms != int(entry.get("start_utc_ms") or -1):
                    violations.append(
                        f"START_TIMESTAMP_TAMPERED_VS_RAW_BYTES:claimed={entry.get('start_utc_ms')},raw={recomputed_event_ms}"
                    )
                t_kind = expected_transport_kind or (
                    capture_receipt.transport_kind if capture_receipt is not None else "INJECTED_FAKE_TRANSPORT"
                )
                parsed_obj = {
                    "symbol": symbol,
                    "product_type": product_type,
                    "endpoint_key": ep_key,
                    "event_utc_ms": recomputed_event_ms,
                    "receive_utc_ms": recv_ms,
                    "bid": round(bid, 2),
                    "ask": round(ask, 2),
                    "spread_usd": round(ask - bid, 4),
                    "transport_kind": t_kind,
                    "prospective_poll_snapshot_only": True,
                }
                recomputed_parsed_sha = canonical_hash(parsed_obj)
    elif origin == "OHLCV_SNAPSHOT":
        if ep_key not in ("candles", "history_candles"):
            violations.append(f"ENDPOINT_ORIGIN_MISMATCH:origin={origin},endpoint={ep_key}")
        elif not isinstance(data, list) or not data:
            violations.append("RAW_BYTES_EMPTY_OR_NONLIST_CANDLES")
        elif bool(body.get("hasMore", False)):
            violations.append("RAW_BYTES_PARTIAL_PAGINATION_HAS_MORE_TRUE")
        else:
            tf_str = str(entry.get("timeframe") or "1m")
            tf_ms = GRANULARITY_TO_MS_C3.get(tf_str)
            if tf_ms is None:
                violations.append(f"UNSUPPORTED_TIMEFRAME:{tf_str}")
            else:
                parsed_bars: List[Dict[str, Any]] = []
                seen_ts: set[int] = set()
                prev_ts: Optional[int] = None
                for idx, row in enumerate(data):
                    if not isinstance(row, (list, tuple)) or len(row) < 6:
                        violations.append(f"RAW_BYTES_MALFORMED_CANDLE_ROW:idx={idx}")
                        break
                    o_ms = int(row[0])
                    c_ms = o_ms + tf_ms
                    o_px, h_px, l_px, c_px, vol = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                    if o_ms in seen_ts:
                        violations.append(f"RAW_BYTES_DUPLICATE_CANDLE_TS:idx={idx}")
                        break
                    seen_ts.add(o_ms)
                    if prev_ts is not None and o_ms <= prev_ts:
                        violations.append(f"RAW_BYTES_NON_MONOTONIC_CANDLES:idx={idx}")
                        break
                    prev_ts = o_ms
                    if c_ms > recv_ms:
                        violations.append(f"RAW_BYTES_UNCLOSED_CANDLE:idx={idx}:close={c_ms}>recv={recv_ms}")
                        break
                    if min(o_px, h_px, l_px, c_px) <= 0.0 or h_px < max(o_px, c_px) or l_px > min(o_px, c_px) or vol < 0.0:
                        violations.append(f"RAW_BYTES_CORRUPT_OHLCV:idx={idx}")
                        break
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
                if parsed_bars and not violations:
                    first_open = parsed_bars[0]["open_time_ms"]
                    last_close = parsed_bars[-1]["close_time_ms"]
                    recomputed_event_ms = last_close
                    if first_open < R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS:
                        violations.append(
                            f"RAW_BYTES_EMBARGO_VIOLATION:start={first_open}<cutoff={R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}"
                        )
                    if len(parsed_bars) != int(entry.get("bar_count") or -1):
                        violations.append(
                            f"BAR_COUNT_TAMPERED_VS_RAW_BYTES:claimed={entry.get('bar_count')},raw={len(parsed_bars)}"
                        )
                    if first_open != int(entry.get("start_utc_ms") or -1) or last_close != int(entry.get("end_utc_ms") or -1):
                        violations.append(
                            f"CANDLE_WINDOW_TAMPERED_VS_RAW_BYTES:claimed=({entry.get('start_utc_ms')},{entry.get('end_utc_ms')}),raw=({first_open},{last_close})"
                        )
                    if last_close != int(entry.get("event_or_close_utc_ms") or -1):
                        violations.append(
                            f"EVENT_TIMESTAMP_TAMPERED_VS_RAW_BYTES:claimed={entry.get('event_or_close_utc_ms')},raw={last_close}"
                        )
                    recomputed_parsed_sha = canonical_hash(parsed_bars)
    else:
        violations.append(f"UNSUPPORTED_DATA_ORIGIN_FOR_RAW_REPARSE:{origin}")

    claimed_parsed_sha = str(entry.get("canonical_parsed_sha256") or "").lower()
    if recomputed_parsed_sha is not None and recomputed_parsed_sha != claimed_parsed_sha:
        violations.append(
            f"CANONICAL_PARSED_SHA256_TAMPERED_VS_RAW_BYTES:claimed={claimed_parsed_sha},recomputed={recomputed_parsed_sha}"
        )

    if capture_receipt is not None:
        if capture_receipt.raw_response_sha256 != actual_raw_sha:
            violations.append("CAPTURE_RECEIPT_RAW_SHA_MISMATCH")
        if recomputed_parsed_sha is not None and capture_receipt.canonical_parsed_sha256 != recomputed_parsed_sha:
            violations.append("CAPTURE_RECEIPT_PARSED_SHA_MISMATCH")
        if recomputed_event_ms is not None and capture_receipt.provider_event_utc_ms != recomputed_event_ms:
            violations.append("CAPTURE_RECEIPT_PROVIDER_EVENT_MS_MISMATCH")

    return {
        "valid": len(violations) == 0,
        "raw_sha256": actual_raw_sha,
        "recomputed_parsed_sha256": recomputed_parsed_sha,
        "recomputed_event_ms": recomputed_event_ms,
        "violations": violations,
    }


def evaluate_c3_prospective_admission(
    entry: Mapping[str, Any],
    *,
    raw_payload_bytes: Optional[bytes],
    capture_receipt: Optional[TrustedCaptureReceiptC3],
    require_live_real_oos: bool = True,
    freeze_timestamp_ms: int = R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
) -> Dict[str, Any]:
    """
    Master P0-A provenance firewall gate evaluated BEFORE and INSIDE escrow lock.
    Cannot be fooled by omitting `is_synthetic_fixture`, setting `partition_role="FRESH_UNTOUCHED"`,
    setting `observed_bid_ask_present=True`, or passing fake transport with `is_fake_transport_qa=False`.
    """
    violations: List[str] = []

    # 1. Base C1/C2 contract validation
    base_val = validate_prospective_snapshot_contract_c1(
        entry,
        freeze_timestamp_ms=freeze_timestamp_ms,
        known_dev_shas=known_dev_shas,
    )
    if not base_val["valid_for_escrow_admission"]:
        violations.extend(base_val["violations"])

    # 2. Synthetic flag explicit guard (even if omitted, receipt check below still blocks!)
    if bool(entry.get("is_synthetic_fixture", False)):
        violations.append("SYNTHETIC_FIXTURE_FLAG_SET")

    # 3. Trusted capture receipt MAC & provenance class check
    provenance_class = ProvenanceClassC3.UNVERIFIED
    if capture_receipt is None:
        violations.append("MISSING_TRUSTED_CAPTURE_RECEIPT")
    else:
        mac_ok, mac_errs = verify_trusted_capture_receipt_mac_c3(capture_receipt)
        if not mac_ok:
            violations.extend(mac_errs)
        else:
            provenance_class = capture_receipt.provenance_class
            if require_live_real_oos and provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP:
                violations.append(
                    f"UNTRUSTED_PROVENANCE_CLASS_FOR_REAL_OOS:{provenance_class.value}"
                )
            if not capture_receipt.real_rate_limiter_active and require_live_real_oos:
                violations.append("LIVE_RATE_LIMITER_NOT_ACTIVE_IN_RECEIPT")

    # 4. Mandatory raw-byte re-parse verification (ZERO metadata-only admission for OOS!)
    reparse = reparse_and_verify_raw_market_bytes_c3(
        raw_payload_bytes,
        entry,
        capture_receipt=capture_receipt if isinstance(capture_receipt, TrustedCaptureReceiptC3) else None,
        freeze_timestamp_ms=freeze_timestamp_ms,
        known_dev_shas=known_dev_shas,
    )
    if not reparse["valid"]:
        violations.extend(reparse["violations"])

    admitted_real_oos = (
        len(violations) == 0
        and require_live_real_oos
        and provenance_class == ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP
    )
    if admitted_real_oos:
        admission_tier = "NEW_REAL_OOS_RAW_VERIFIED"
    elif (
        (not require_live_real_oos)
        and len(violations) == 0
        and provenance_class == ProvenanceClassC3.QA_FAKE_TRANSPORT
    ):
        admission_tier = "TEST_ONLY_NOT_ELIGIBLE_OOS"
    elif raw_payload_bytes is None:
        admission_tier = "REJECTED_METADATA_ONLY_FORBIDDEN"
    else:
        admission_tier = "REJECTED_PROVENANCE_FIREWALL"

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "admitted": len(violations) == 0,
        "eligible_for_fresh_oos": admitted_real_oos,
        "provenance_class": provenance_class.value,
        "admission_tier": admission_tier,
        "raw_reparse_verified": bool(reparse["valid"]),
        "raw_sha256": reparse.get("raw_sha256"),
        "recomputed_parsed_sha256": reparse.get("recomputed_parsed_sha256"),
        "violations": violations,
        "tcb_disclosure": TCB_LIMITATIONS_DISCLOSURE_C3,
    }
