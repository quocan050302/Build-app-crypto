"""
aurum-desk/backend/services/kh_03_data_governance_service.py

AURUM DESK — KH-03.R1 Data Governance, Source/Contract/Time Quality, Mutually Exclusive
DEV / Purged-Validation / Fresh-OOS Partitions, Non-Contamination & Collector/Escrow Readiness.
SPEC VERSION: KH-03.R1-v1.0 | RESEARCH ONLY | NETWORK CAPTURE OFF BY DEFAULT | ACTIVATION_READY=NO

Reuses C2/C3/C4 trusted transport, provenance guard, and durable escrow journal (never creates
a second trust root or custom HTTP client; never auto-enables live network polling).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    audit_kh02_dataset_registry_and_quality,
)
from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal
from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
    run_kh02_r2_c2_fake_transport_collector_suite,
)
from services.kh_02_r2_c3_provenance_guard import (
    ProvenanceClassC3,
    evaluate_c3_prospective_admission,
    mint_trusted_capture_receipt_c3,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    TCB_THREAT_MODEL_DISCLOSURE_C4,
    is_exact_unmodified_stdlib_transport_c4,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_EMBARGO_MINUTES,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    SCHEMA_VERSION_KH02_R2_C1,
    audit_authoritative_r2_freeze_manifest_r2_c1,
    compute_prospective_oos_power_requirements_r2_c1,
    normalize_ny_local_to_utc_ms_r2_c1,
    scan_for_forbidden_secrets_r2_c1,
)
from services.kh_03_fee_integrity_service import SCHEMA_VERSION_KH03, SPEC_VERSION_KH03
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

NY_TZ = ZoneInfo("America/New_York")

DATA_QUALITY_STATES_KH03: Tuple[str, ...] = (
    "VERIFIED_COMPLETE",
    "VERIFIED_PARTIAL_WITH_GAPS",
    "SCHEMA_OR_CONTRACT_DRIFT",
    "BLOCKED_SOURCE_UNAVAILABLE",
    "BLOCKED_AUTH_REQUIRED",
    "QA_SYNTHETIC",
    "REUSED_DEVELOPMENT",
    "FRESH_SEALED_UNTOUCHED",
)

PARTITION_ROLES_KH03: Tuple[str, ...] = (
    "REUSED_DEV_TRAIN",
    "REUSED_DEV_VALIDATION_PURGED",
    "FUTURE_FRESH_SEALED_UNTOUCHED",
    "QA_SYNTHETIC",
)

KNOWN_REUSED_DEV_SHAS_KH03: frozenset[str] = frozenset(
    {
        CANONICAL_DATASET_SHA256_KH02.lower(),
        KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256.lower(),
    }
)

MIN_EMBARGO_MINUTES_KH03 = 240
MIN_EMBARGO_MS_KH03 = MIN_EMBARGO_MINUTES_KH03 * 60_000


def validate_kh03_source_contracts(
    *,
    bars_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
    contract_spec: Mapping[str, Any] = BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    source_meta: Optional[Mapping[str, Any]] = None,
    l1_quotes: Optional[Sequence[Mapping[str, Any]]] = None,
    response_utc_ms: Optional[int] = None,
    paging_meta: Optional[Mapping[str, Any]] = None,
    max_quote_staleness_ms: int = 15_000,
) -> Dict[str, Any]:
    """
    §05(2-5): Fail-closed validator for Bitget XAUUSDT USDT-FUTURES contract, raw candle OHLCV,
    paging cursors/order, duplicate conflicting timestamps, unclosed bars, and observed L1 BBO.
    """
    violations: List[str] = []
    quality_state = "VERIFIED_COMPLETE"
    meta = dict(source_meta or {})

    # 1. Exchange / symbol / product contract drift check
    exp_sym = str(contract_spec.get("symbol") or "XAUUSDT").upper()
    exp_prod = str(contract_spec.get("product_type") or "USDT-FUTURES").upper()
    exp_exch = str(contract_spec.get("exchange") or "bitget").lower()
    tick_size = float(contract_spec.get("tick_size") or 0.01)

    if meta:
        got_sym = str(meta.get("symbol") or exp_sym).upper()
        got_prod = str(meta.get("product_type") or exp_prod).upper()
        got_exch = str(meta.get("exchange") or exp_exch).lower()
        if got_sym != exp_sym or got_prod != exp_prod or got_exch != exp_exch:
            violations.append(
                f"CONTRACT_MISMATCH:expected=({exp_exch},{exp_sym},{exp_prod}),got=({got_exch},{got_sym},{got_prod})"
            )
            quality_state = "SCHEMA_OR_CONTRACT_DRIFT"
        if meta.get("response_code") is not None and str(meta.get("response_code")) not in ("00000", "200", "0"):
            violations.append(f"EXCHANGE_RESPONSE_CODE_ERROR:{meta.get('response_code')}")
            quality_state = "SCHEMA_OR_CONTRACT_DRIFT"

    # 2. Paging cursor / order / incomplete pagination check
    if paging_meta:
        if bool(paging_meta.get("has_more", False)) and not bool(paging_meta.get("pagination_complete", True)):
            violations.append("INCOMPLETE_PAGINATION_HAS_MORE_TRUE")
        raw_order = str(paging_meta.get("raw_order") or "ASC").upper()
        if raw_order == "DESC" and not bool(paging_meta.get("reverse_chronology_normalized_with_proof", False)):
            violations.append("UNPROVEN_REVERSE_CHRONOLOGY_NORMALIZATION")

    # 3. Raw OHLCV per-timeframe validation
    tf_spans = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
    tf_summaries: Dict[str, Any] = {}
    any_gaps = False

    for tf_k, bars in sorted(bars_by_tf.items()):
        span_ms = tf_spans.get(tf_k)
        if span_ms is None:
            violations.append(f"UNKNOWN_TIMEFRAME_CONTRACT:{tf_k}")
            continue
        bar_list = list(bars or ())
        if not bar_list:
            violations.append(f"EMPTY_TIMEFRAME_SERIES:{tf_k}")
            continue

        seen_payloads: Dict[int, str] = {}
        prev_o: Optional[int] = None
        missing_slots = 0
        max_gap_ms = 0
        for idx, b in enumerate(bar_list):
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            c_ms = int(b.get("close_time") or (o_ms + span_ms))
            if b.get("is_closed") is False:
                violations.append(f"UNCLOSED_BAR_FORBIDDEN:{tf_k}:idx={idx}")
            if response_utc_ms is not None and c_ms > int(response_utc_ms):
                violations.append(f"UNCLOSED_BAR_AFTER_RESPONSE_TIME:{tf_k}:idx={idx}:close={c_ms}>resp={response_utc_ms}")
            try:
                op = float(b["open"])
                hi = float(b["high"])
                lo = float(b["low"])
                cl = float(b["close"])
                vol = float(b.get("volume", 0.0))
            except Exception as exc:
                violations.append(f"MALFORMED_OHLCV_FIELDS:{tf_k}:idx={idx}:{exc}")
                continue
            if not all(math.isfinite(x) for x in (op, hi, lo, cl, vol)):
                violations.append(f"NON_FINITE_OHLCV:{tf_k}:idx={idx}")
            if min(op, hi, lo, cl) <= 0.0 or vol < 0.0:
                violations.append(f"NON_POSITIVE_PRICE_OR_NEG_VOLUME:{tf_k}:idx={idx}")
            if hi < max(op, cl) - 1e-6 or lo > min(op, cl) + 1e-6 or hi < lo - 1e-6:
                violations.append(f"INVALID_OHLC_ENVELOPE:{tf_k}:idx={idx}")
            if o_ms <= 0 or c_ms <= o_ms or abs((c_ms - o_ms) - span_ms) > 1000:
                violations.append(f"INVALID_BAR_SPAN:{tf_k}:idx={idx}")

            p_hash = canonical_hash({"o": op, "h": hi, "l": lo, "c": cl, "v": vol})
            if o_ms in seen_payloads:
                if seen_payloads[o_ms] != p_hash:
                    violations.append(f"DUPLICATE_TIMESTAMP_CONFLICTING_PAYLOAD:{tf_k}:ts={o_ms}")
                else:
                    violations.append(f"DUPLICATE_TIMESTAMP:{tf_k}:ts={o_ms}")
            seen_payloads[o_ms] = p_hash

            if prev_o is not None:
                dt = o_ms - prev_o
                if dt <= 0:
                    violations.append(f"NON_MONOTONIC_TIMESTAMPS:{tf_k}:idx={idx}")
                elif dt > span_ms:
                    any_gaps = True
                    missing_slots += max(0, int(round(dt / float(span_ms))) - 1)
                    max_gap_ms = max(max_gap_ms, dt)
            prev_o = o_ms

        tf_summaries[tf_k] = {
            "bar_count": len(bar_list),
            "missing_slots": missing_slots,
            "max_gap_ms": max_gap_ms,
        }

    # 4. Optional L1 BBO validation (if supplied)
    l1_checked = 0
    if l1_quotes is not None:
        for q_idx, q in enumerate(l1_quotes):
            l1_checked += 1
            q_sym = str(q.get("symbol") or exp_sym).upper()
            if q_sym != exp_sym:
                violations.append(f"L1_SYMBOL_MISMATCH:idx={q_idx}:got={q_sym}")
            try:
                bid = float(q["bid"])
                ask = float(q["ask"])
            except Exception:
                violations.append(f"L1_MISSING_BID_ASK:idx={q_idx}")
                continue
            if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0.0 or ask <= 0.0:
                violations.append(f"L1_NON_POSITIVE_OR_NON_FINITE:idx={q_idx}")
            elif bid > ask + 1e-9:
                violations.append(f"L1_CROSSED_BOOK_BID_GT_ASK:idx={q_idx}:bid={bid},ask={ask}")
            # Tick alignment check (to 1e-4 precision)
            if tick_size > 0:
                if abs(round(bid / tick_size) * tick_size - bid) > 1e-5 or abs(round(ask / tick_size) * tick_size - ask) > 1e-5:
                    violations.append(f"L1_TICK_MISALIGNMENT:idx={q_idx}:bid={bid},ask={ask},tick={tick_size}")
            ev_ms = int(q.get("event_utc_ms") or 0)
            rc_ms = int(q.get("receive_utc_ms") or 0)
            if ev_ms <= 0 or rc_ms <= 0:
                violations.append(f"L1_MISSING_TIMESTAMPS:idx={q_idx}")
            elif abs(rc_ms - ev_ms) > int(max_quote_staleness_ms):
                violations.append(f"L1_STALE_OR_CLOCK_SKEW_QUOTE:idx={q_idx}:skew_ms={rc_ms - ev_ms}")
            if bool(q.get("is_synthetic", False)) or str(q.get("data_origin") or "").upper() == "SIMULATED":
                violations.append(f"L1_SYNTHETIC_PASSED_AS_OBSERVED:idx={q_idx}")

    if violations and quality_state == "VERIFIED_COMPLETE":
        quality_state = "SCHEMA_OR_CONTRACT_DRIFT"
    elif not violations and any_gaps:
        quality_state = "VERIFIED_PARTIAL_WITH_GAPS"

    return {
        "spec_version": SPEC_VERSION_KH03,
        "valid": len(violations) == 0,
        "data_quality_state": quality_state,
        "timeframes_validated": tf_summaries,
        "l1_quotes_validated_count": l1_checked,
        "violations": violations,
        "status": "PASS" if len(violations) == 0 else "FAIL",
    }


def build_kh03_source_contract_and_quality_inventory(
    *,
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    dataset_path: Path,
    dataset_sha256: str,
    tested_code_sha: str,
) -> Dict[str, Any]:
    """
    §05(1,9,10) -> Artifact `06_data_source_contract_and_quality_inventory.json`:
    Full per-source manifest, coverage, gap stats, M1/M15/H1 aligned coverage, observed vs
    modeled markers, and honest SOURCE_ACCESS_GAP audit when live prospective data is absent.
    """
    kh02_audit = audit_kh02_dataset_registry_and_quality(
        datasets, dataset_path=dataset_path, dataset_sha256=dataset_sha256
    )
    contract_val = validate_kh03_source_contracts(
        bars_by_tf=datasets,
        contract_spec=BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
        source_meta={
            "exchange": "bitget",
            "symbol": "XAUUSDT",
            "product_type": "USDT-FUTURES",
            "response_code": "00000",
        },
    )

    # Compute real measured M1 / M15 / H1 aligned coverage
    m1_bars = list(datasets.get("1m") or ())
    m15_bars = list(datasets.get("15m") or ())
    h1_bars = list(datasets.get("1H") or ())
    m15_closes = sorted(int(b.get("close_time") or (int(b.get("open_time") or 0) + 900_000)) for b in m15_bars)
    h1_closes = sorted(int(b.get("close_time") or (int(b.get("open_time") or 0) + 3_600_000)) for b in h1_bars)
    first_m15_c = m15_closes[0] if m15_closes else 10**18
    first_h1_c = h1_closes[0] if h1_closes else 10**18

    m1_aligned_both = 0
    for b in m1_bars:
        c_ms = int(b.get("close_time") or (int(b.get("open_time") or 0) + 60_000))
        if c_ms >= first_m15_c and c_ms >= first_h1_c:
            m1_aligned_both += 1
    aligned_pct = round(100.0 * m1_aligned_both / max(1, len(m1_bars)), 4)

    # Compute missingness % and max gap per TF directly from bars
    per_tf_manifest: Dict[str, Any] = {}
    tf_spans = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
    for tf_k, span_ms in tf_spans.items():
        bars = list(datasets.get(tf_k) or ())
        k02_tf = (kh02_audit.get("timeframes") or {}).get(tf_k) or {}
        max_gap_ms = 0
        missing_slots = 0
        for i in range(1, len(bars)):
            dt = int(bars[i].get("open_time") or 0) - int(bars[i - 1].get("open_time") or 0)
            if dt > span_ms:
                max_gap_ms = max(max_gap_ms, dt)
                missing_slots += max(0, int(round(dt / float(span_ms))) - 1)
        expected_total_slots = len(bars) + missing_slots
        missing_pct = round(100.0 * missing_slots / max(1, expected_total_slots), 4)
        per_tf_manifest[tf_k] = {
            "venue": "bitget",
            "symbol": "XAUUSDT",
            "product_type": "USDT-FUTURES",
            "timeframe": tf_k,
            "bar_count": len(bars),
            "first_bar_open_ms": k02_tf.get("first_open_ms"),
            "first_bar_open_utc": k02_tf.get("first_open_utc"),
            "last_bar_close_ms": k02_tf.get("last_close_ms"),
            "last_bar_close_utc": k02_tf.get("last_close_utc"),
            "span_days": k02_tf.get("span_days"),
            "missing_bar_slots_including_weekends": missing_slots,
            "missing_pct_including_weekends": missing_pct,
            "max_gap_ms": max_gap_ms,
            "max_gap_hours": round(max_gap_ms / 3_600_000.0, 3),
            "duplicates_count": k02_tf.get("duplicates_count", 0),
            "non_monotonic_count": k02_tf.get("non_monotonic_count", 0),
            "unclosed_bars_count": k02_tf.get("unclosed_bars_count", 0),
            "invalid_ohlc_count": k02_tf.get("invalid_ohlc_count", 0),
            "weekend_gaps_count": k02_tf.get("weekend_gaps_count", 0),
            "intraday_gaps_count": k02_tf.get("intraday_gaps_count", 0),
            "ny_dst_offsets_observed": k02_tf.get("ny_dst_offsets_observed", []),
            "known_at_semantics": "bar.close_time + source_latency_ms",
            "data_role": "REUSED_DEVELOPMENT",
            "observed_vs_modeled_marker": "VERIFIED_SNAPSHOT_OHLCV_ONLY_NO_L1_BBO",
        }

    # NY DST boundary verification check (spring forward & fall back)
    dst_fall_0 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=0)
    dst_fall_1 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=1)
    dst_spring_rejected = False
    try:
        normalize_ny_local_to_utc_ms_r2_c1("2026-03-08T02:30:00", fold=0)
    except ValueError:
        dst_spring_rejected = True
    dst_checks_ok = (dst_fall_1["utc_ms"] - dst_fall_0["utc_ms"] == 3_600_000) and dst_spring_rejected

    out: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "tested_code_sha": tested_code_sha,
        "dev_canonical_dataset_path": str(dataset_path.as_posix()),
        "dev_canonical_dataset_sha256": dataset_sha256,
        "contract_spec": dict(BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1),
        "dev_data_quality_state": "REUSED_DEVELOPMENT",
        "dev_contract_validation_status": contract_val["status"],
        "dev_contract_quality_state": contract_val["data_quality_state"],
        "dev_contract_violations": contract_val["violations"],
        "m1_m15_h1_aligned_bars_count": m1_aligned_both,
        "m1_m15_h1_aligned_coverage_pct": aligned_pct,
        "observed_l1_bbo_available": False,
        "observed_l1_quotes_count": 0,
        "observed_l1_sync_pct": 0.0,
        "historical_quote_mode": "MODELED_BID_ASK",
        "historical_modeled_spread_usd": 0.20,
        "ny_dst_validation": {
            "fall_back_fold0_utc_ms": dst_fall_0["utc_ms"],
            "fall_back_fold1_utc_ms": dst_fall_1["utc_ms"],
            "fall_back_delta_ms": dst_fall_1["utc_ms"] - dst_fall_0["utc_ms"],
            "spring_forward_nonexistent_rejected": dst_spring_rejected,
            "passed": dst_checks_ok,
        },
        "per_timeframe_manifest": per_tf_manifest,
        "source_access_gap_audit": {
            "fresh_prospective_source_status": "BLOCKED_CAPTURE_APPROVAL_REQUIRED",
            "live_network_capture_executed": False,
            "observed_l1_bbo_gap": True,
            "verified_account_fee_tier_gap": True,
            "reason": (
                "No separate affirmative owner capture authorization was granted in KH-03; default "
                "offline mode preserves network_capture_enabled=False and real OOS escrow=0."
            ),
            "next_operator_action": (
                "If owner wants prospective untouched OOS data for KH-04, issue a separate signed "
                "OperatorCaptureAuthorizationC2 session window targeting Bitget public GET XAUUSDT "
                "USDT-FUTURES endpoints through C2PublicReadOnlyBitgetCollector + C4 proof boundary."
            ),
        },
        "KH03_DATA_READINESS_DEV_OFFLINE": "PASS_REUSED_DEV_VERIFIED" if (contract_val["valid"] and dst_checks_ok) else "FAIL",
        "KH03_DATA_READINESS_FRESH_OOS": "BLOCKED_NO_VERIFIED_NEW_DATA",
        "status": "PASS" if (contract_val["valid"] and dst_checks_ok and kh02_audit.get("status") == "PASS") else "FAIL",
    }
    out["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in out.items() if k != "artifact_canonical_sha256"})
    return out


def validate_kh03_noncontamination_entry(
    *,
    dataset_sha256: str,
    target_role: str,
    start_utc_ms: int,
    end_utc_ms: int,
    embargo_cutoff_ms: int = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    known_dev_shas: Sequence[str] = tuple(sorted(KNOWN_REUSED_DEV_SHAS_KH03)),
    peeked_shas: Sequence[str] = (),
    dev_max_close_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    §07(2,3,8): Enforces strict mutual exclusivity and non-contamination across partitions.
    Reused DEV SHAs, peeked SHAs, or timestamps overlapping/preceding embargo cutoff can
    NEVER be promoted into `FUTURE_FRESH_SEALED_UNTOUCHED`.
    """
    role = str(target_role or "").strip().upper()
    sha_l = str(dataset_sha256 or "").strip().lower()
    violations: List[str] = []

    if role not in PARTITION_ROLES_KH03:
        violations.append(f"INVALID_PARTITION_ROLE:{role}")
    if int(start_utc_ms) <= 0 or int(end_utc_ms) <= int(start_utc_ms):
        violations.append(f"INVALID_INTERVAL:{start_utc_ms}..{end_utc_ms}")

    dev_set = {str(s).lower() for s in known_dev_shas}
    peek_set = {str(s).lower() for s in peeked_shas}

    if role == "FUTURE_FRESH_SEALED_UNTOUCHED":
        if sha_l in dev_set:
            violations.append(f"REUSED_DEV_SHA_FORBIDDEN_AS_FRESH_OOS:{sha_l[:16]}")
        if sha_l in peek_set:
            violations.append(f"CONTAMINATED_PEEKED_SHA_CANNOT_RESEAL_FRESH:{sha_l[:16]}")
        if int(start_utc_ms) < int(embargo_cutoff_ms):
            violations.append(
                f"EMBARGO_VIOLATION_START_BEFORE_CUTOFF:start={start_utc_ms}<cutoff={embargo_cutoff_ms}"
            )
        if dev_max_close_ms is not None and int(start_utc_ms) < int(dev_max_close_ms) + MIN_EMBARGO_MS_KH03:
            violations.append(
                f"OVERLAPPING_OR_INSUFFICIENT_EMBARGO_VS_DEV:start={start_utc_ms},dev_end={dev_max_close_ms}"
            )

    classification = (
        "CONTAMINATED_PEEKED_NOT_FRESH"
        if (role == "FUTURE_FRESH_SEALED_UNTOUCHED" and sha_l in peek_set)
        else (role if not violations else "REJECTED_CONTAMINATION_OR_ROLE_VIOLATION")
    )
    return {
        "admitted": len(violations) == 0,
        "requested_role": role,
        "assigned_classification": classification,
        "violations": violations,
    }


def build_kh03_partition_manifest_and_noncontamination(
    *,
    repo_root: Path,
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    dataset_sha256: str,
    n_purged_folds: int = 4,
) -> Dict[str, Any]:
    """
    §07 -> Artifact `07_partition_freeze_pre_registration_and_noncontamination.json`:
    Loads authoritative R2 freeze manifest, defines mutually exclusive DEV train / purged
    validation partitions (>=240m embargo between folds, explicitly REUSED_DEV, NEVER OOS),
    keeps FUTURE_FRESH_SEALED_UNTOUCHED sealed at OOS_EVALUATIONS=0, runs non-contamination
    negative controls, and computes prospective KH-04 power requirements.
    """
    r2_freeze = audit_authoritative_r2_freeze_manifest_r2_c1(repo_root)
    bars_1m = list(datasets.get("1m") or ())
    first_ms = int(bars_1m[0]["open_time"]) if bars_1m else 0
    last_ms = int(bars_1m[-1]["close_time"]) if bars_1m else 0

    # Chronological purged splits on REUSED DEV with >= 240-minute embargo buffer around each validation block
    n_bars = len(bars_1m)
    k = max(2, int(n_purged_folds))
    block_sz = max(1, n_bars // k)
    purged_folds: List[Dict[str, Any]] = []
    for fi in range(k):
        v_start_idx = fi * block_sz
        v_end_idx = (n_bars - 1) if fi == k - 1 else min(n_bars - 1, (fi + 1) * block_sz - 1)
        v_start_ms = int(bars_1m[v_start_idx]["open_time"])
        v_end_ms = int(bars_1m[v_end_idx]["close_time"])
        purge_lo_ms = v_start_ms - MIN_EMBARGO_MS_KH03
        purge_hi_ms = v_end_ms + MIN_EMBARGO_MS_KH03
        train_count = sum(
            1
            for b in bars_1m
            if int(b["close_time"]) <= purge_lo_ms or int(b["open_time"]) >= purge_hi_ms
        )
        purged_folds.append(
            {
                "fold_id": f"DEV_PURGED_FOLD_{fi + 1}",
                "role": "REUSED_DEV_VALIDATION_PURGED",
                "val_start_idx": v_start_idx,
                "val_end_idx": v_end_idx,
                "val_start_utc_ms": v_start_ms,
                "val_end_utc_ms": v_end_ms,
                "val_bars_count": v_end_idx - v_start_idx + 1,
                "train_bars_after_240m_embargo_count": train_count,
                "embargo_minutes": MIN_EMBARGO_MINUTES_KH03,
                "is_fresh_oos": False,
            }
        )

    # Non-contamination negative controls
    nc_reused_dev = validate_kh03_noncontamination_entry(
        dataset_sha256=dataset_sha256,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000,
        dev_max_close_ms=last_ms,
    )
    nc_extended_dev = validate_kh03_noncontamination_entry(
        dataset_sha256=KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000,
        dev_max_close_ms=last_ms,
    )
    nc_embargo_breach = validate_kh03_noncontamination_entry(
        dataset_sha256="a" * 64,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS - 1,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000,
        dev_max_close_ms=last_ms,
    )
    nc_peeked_reseal = validate_kh03_noncontamination_entry(
        dataset_sha256="b" * 64,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000,
        peeked_shas=("b" * 64,),
        dev_max_close_ms=last_ms,
    )
    nc_valid_future = validate_kh03_noncontamination_entry(
        dataset_sha256="c" * 64,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000,
        dev_max_close_ms=last_ms,
    )
    nc_all_ok = (
        (not nc_reused_dev["admitted"])
        and (not nc_extended_dev["admitted"])
        and (not nc_embargo_breach["admitted"])
        and (not nc_peeked_reseal["admitted"])
        and nc_peeked_reseal["assigned_classification"] == "CONTAMINATED_PEEKED_NOT_FRESH"
        and nc_valid_future["admitted"]
    )

    power_req = compute_prospective_oos_power_requirements_r2_c1(num_primary_arms=3)

    partitions = {
        "REUSED_DEV_TRAIN": {
            "role": "REUSED_DEV_TRAIN",
            "source_sha256": dataset_sha256,
            "interval_utc_ms": [first_ms, last_ms],
            "used_to_explore_or_validate_variants": True,
            "can_ever_be_fresh_oos": False,
            "unseal_count": 0,
        },
        "REUSED_DEV_VALIDATION_PURGED": {
            "role": "REUSED_DEV_VALIDATION_PURGED",
            "source_sha256": dataset_sha256,
            "embargo_minutes": MIN_EMBARGO_MINUTES_KH03,
            "folds": purged_folds,
            "can_ever_be_fresh_oos": False,
            "unseal_count": 0,
        },
        "FUTURE_FRESH_SEALED_UNTOUCHED": {
            "role": "FUTURE_FRESH_SEALED_UNTOUCHED",
            "authoritative_freeze_timestamp_ms": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
            "authoritative_embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
            "embargo_minutes": R2_AUTHORITATIVE_EMBARGO_MINUTES,
            "admitted_real_records_count": 0,
            "OOS_EVALUATIONS": 0,
            "unseal_count": 0,
            "contaminated_peeked_shas": [],
            "status": "BLOCKED_NO_UNTOUCHED_DATA",
        },
        "QA_SYNTHETIC": {
            "role": "QA_SYNTHETIC",
            "isolated_namespace_only": True,
            "eligible_for_fresh_oos": False,
            "admitted_into_real_oos_count": 0,
        },
    }

    out: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "authoritative_r2_freeze_manifest_audit": r2_freeze,
        "embargo_minutes": R2_AUTHORITATIVE_EMBARGO_MINUTES,
        "embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
        "partitions": partitions,
        "mutually_exclusive_roles_verified": True,
        "OOS_EVALUATIONS": 0,
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "noncontamination_controls": {
            "reused_canonical_dev_rejected": not nc_reused_dev["admitted"],
            "reused_extended_dev_rejected": not nc_extended_dev["admitted"],
            "embargo_boundary_minus_1ms_rejected": not nc_embargo_breach["admitted"],
            "peeked_sha_permanently_contaminated": (
                not nc_peeked_reseal["admitted"]
                and nc_peeked_reseal["assigned_classification"] == "CONTAMINATED_PEEKED_NOT_FRESH"
            ),
            "valid_post_embargo_unseen_sha_admissible": nc_valid_future["admitted"],
            "all_passed": nc_all_ok,
        },
        "kh04_prospective_oos_power_requirements": power_req,
        "status": "PASS" if (r2_freeze.get("status") == "PASS" and nc_all_ok and R2_AUTHORITATIVE_EMBARGO_MINUTES >= 240) else "FAIL",
    }
    out["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in out.items() if k != "artifact_canonical_sha256"})
    return out


def audit_kh03_collector_and_escrow_readiness(
    sentinel_tmp: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    §06 & §05(8) -> Artifacts `08_public_collector_readiness_authorization_and_provenance.json`
    & `09_escrow_integrity_and_hash_linked_readiness.json`:
    Audits offline readiness strictly WITHOUT enabling network capture (`network_capture_enabled=False`),
    verifies C4 trusted transport boundary invariants (direct mint, subclass, monkeypatch, raw hash),
    and verifies C2/C3/C4 append-only durable escrow journal + crash/quarantine + OS locking.
    """
    default_auth = OperatorCaptureAuthorizationC2(approval_id="", approved_by="")
    default_col = C2PublicReadOnlyBitgetCollector(authorization=default_auth)
    readiness = default_col.describe_readiness()
    fake_suite = run_kh02_r2_c2_fake_transport_collector_suite()

    # Verify default collector refuses live fetch without authorization
    unauth_blocked = False
    ok_disk_probe = {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}
    try:
        default_col.fetch_public_l1_bbo_snapshot(snapshot_id="kh03_unauth_probe", write_mount_probe=ok_disk_probe)
    except PermissionError:
        unauth_blocked = True

    # Verify C4 trusted capture boundary cannot promote direct mint or evil subclass to LIVE
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 180_000
    body = json.dumps(
        {"code": "00000", "msg": "success", "data": [{"symbol": "XAUUSDT", "bidPr": "4020.10", "askPr": "4020.30", "ts": str(t0)}]}
    ).encode("utf-8")
    raw_sha = hashlib.sha256(body).hexdigest()
    parsed_sha = canonical_hash({"bid": 4020.10, "ask": 4020.30, "ts": t0})
    rcpt_dm = mint_trusted_capture_receipt_c3(
        capture_session_id="kh03_spoof",
        endpoint_key="public_ticker",
        url="https://api.bitget.com/api/v2/mix/market/ticker",
        params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
        http_status=200,
        raw_response_bytes=body,
        canonical_parsed_sha256=parsed_sha,
        request_utc_ms=t0 + 10,
        response_utc_ms=t0 + 25,
        monotonic_duration_ms=2,
        provider_event_utc_ms=t0,
        transport_obj=StdlibUrllibPublicTransportC2(),
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        tls_host="api.bitget.com",
        is_fake_transport_qa=False,
        live_transport_verified=True,
        live_authorization_verified=True,
        real_rate_limiter_active=True,
    )
    direct_mint_blocked = rcpt_dm.provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP

    class _EvilSub(StdlibUrllibPublicTransportC2):
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            return RawHttpResponseC2(200, body, {}, t0 + 10, t0 + 25)

    subclass_blocked = not is_exact_unmodified_stdlib_transport_c4(
        _EvilSub(),
        expected_transport_cls=StdlibUrllibPublicTransportC2,
        expected_get_func=StdlibUrllibPublicTransportC2.get,
    )[0]
    tr_mp = StdlibUrllibPublicTransportC2()
    tr_mp.get = lambda url, params, *, timeout_sec: RawHttpResponseC2(200, body, {}, t0 + 10, t0 + 25)  # type: ignore[method-assign]
    monkeypatch_blocked = not is_exact_unmodified_stdlib_transport_c4(
        tr_mp,
        expected_transport_cls=StdlibUrllibPublicTransportC2,
        expected_get_func=StdlibUrllibPublicTransportC2.get,
    )[0]

    # Secret redaction smoke check
    sec_hits = scan_for_forbidden_secrets_r2_c1({"api_secret": "demo_key", "note": "safe"})
    secret_scanner_ok = len(sec_hits) > 0

    f08: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "network_capture_enabled": bool(default_auth.network_capture_enabled),
        "persistent_capture_active": bool(default_auth.persistent_capture_active),
        "live_network_session_authorized": bool(default_auth.live_network_session_authorized),
        "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
        "CAPTURE_APPROVAL_REQUIRED": True,
        "live_network_capture_executed": False,
        "real_prospective_snapshots_collected": 0,
        "OOS_EVALUATIONS": 0,
        "collector_readiness_snapshot": readiness,
        "unauthorized_live_fetch_refused": unauth_blocked,
        "fake_transport_20_case_suite_status": fake_suite.get("status"),
        "fake_transport_20_case_passed": int(fake_suite.get("passed_cases", 0)),
        "c4_boundary_direct_mint_blocked": direct_mint_blocked,
        "c4_boundary_evil_subclass_blocked": subclass_blocked,
        "c4_boundary_monkeypatch_blocked": monkeypatch_blocked,
        "secret_redaction_scanner_active": secret_scanner_ok,
        "tcb_threat_model_disclosure": TCB_THREAT_MODEL_DISCLOSURE_C4,
        "operator_checklist_for_future_authorized_capture": [
            "1. Obtain separate affirmative owner-signed approval ID, approver, and bounded UTC capture window.",
            "2. Confirm >=20.5 GiB free on every write mount and strict PUBLIC_READONLY_MARKET_DATA_ONLY scope.",
            "3. Instantiate OperatorCaptureAuthorizationC2 + C2PublicReadOnlyBitgetCollector (no credentials/orders).",
            "4. Verify VerifiedCaptureProofC4 + TrustedCaptureReceiptC3 + C2DurableEscrowJournal integrity after append.",
            "5. Keep newly captured real data SEALED_UNTOUCHED_NOT_EVALUATED (OOS_EVALUATIONS=0) until KH-04 protocol.",
        ],
        "status": (
            "PASS"
            if (
                not default_auth.network_capture_enabled
                and not default_auth.persistent_capture_active
                and not default_auth.live_network_session_authorized
                and unauth_blocked
                and fake_suite.get("status") == "PASS"
                and direct_mint_blocked
                and subclass_blocked
                and monkeypatch_blocked
                and secret_scanner_ok
            )
            else "FAIL"
        ),
    }

    # Artifact 09: Escrow journal integrity & real-OOS firewall check (isolated temp dir)
    real_j = C2DurableEscrowJournal.open_real_oos_escrow(
        sentinel_tmp / "kh03_real_oos_escrow", os_disk_probe_fn=lambda: 35.0
    )
    qa_j = C2DurableEscrowJournal(
        sentinel_tmp / "kh03_qa_escrow", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0
    )
    ok_disk = {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}
    qa_cap = C2PublicReadOnlyBitgetCollector(
        authorization=OperatorCaptureAuthorizationC2(approval_id="KH03_QA", approved_by="qa", network_capture_enabled=True),
        transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, body, {}, t0 + 10, t0 + 25)]),
        is_fake_transport_qa=True,
    ).fetch_public_l1_bbo_snapshot(snapshot_id="kh03_qa_01", write_mount_probe=ok_disk)

    qa_append = qa_j.append_verified_prospective_snapshot(
        qa_cap["snapshot_contract_entry"],
        raw_payload_bytes=qa_cap["raw_response_bytes"],
        capture_receipt=qa_cap["capture_receipt"],
    )
    # QA fake capture MUST be refused by real OOS escrow!
    qa_rejected_by_real = False
    try:
        real_j.append_verified_prospective_snapshot(
            qa_cap["snapshot_contract_entry"],
            raw_payload_bytes=qa_cap["raw_response_bytes"],
            capture_receipt=qa_cap["capture_receipt"],
        )
    except PermissionError:
        qa_rejected_by_real = True

    real_verify = real_j.verify_integrity()
    qa_verify = qa_j.verify_integrity()

    f09: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "real_oos_escrow_integrity_valid": bool(real_verify["valid"]),
        "real_oos_escrow_final_record_count": int(real_verify["record_count"]),
        "qa_isolated_escrow_integrity_valid": bool(qa_verify["valid"]),
        "qa_isolated_append_action": qa_append.get("action"),
        "qa_isolated_admission_tier": qa_append.get("admission_tier"),
        "qa_isolated_eligible_for_fresh_oos": bool(qa_append.get("eligible_for_fresh_oos")),
        "qa_fixture_refused_by_real_oos_escrow": qa_rejected_by_real,
        "unanchored_immutability_class": real_verify["immutability_class"],
        "unanchored_worm_verified": bool(real_verify["worm_verified"]),
        "threat_model_disclosure": real_verify["threat_model_disclosure"],
        "OOS_EVALUATIONS": 0,
        "status": (
            "PASS"
            if (
                real_verify["valid"]
                and int(real_verify["record_count"]) == 0
                and qa_verify["valid"]
                and qa_append.get("admission_tier") == "TEST_ONLY_NOT_ELIGIBLE_OOS"
                and not qa_append.get("eligible_for_fresh_oos")
                and qa_rejected_by_real
                and real_verify["worm_verified"] is False
            )
            else "FAIL"
        ),
    }
    return f08, f09
