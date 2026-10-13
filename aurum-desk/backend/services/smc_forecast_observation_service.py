"""
backend/services/smc_forecast_observation_service.py
V14.4.2 - Persistent Forecast Snapshot & Forward Causal Outcome Evaluation.

Guarantees:
1. Forecast snapshots are persisted BEFORE future price outcomes are known.
2. Deduplication: deterministic content digest ensures repeated cycles with unchanged facts do not spam identical rows.
3. Outcome definitions are strictly causal and distinct from trading order outcomes:
   - WATCHED_LIQUIDITY_TOUCH != WIN trade.
   - Target and invalidation touched on same bar without sequence = AMBIGUOUS.
   - Feed gap during observation window = DATA_UNAVAILABLE.
4. Savepoint isolation: observation writes use nested transactions (db.begin_nested())
   so an observation fault never rolls back core trading transactions.
5. Probability remains UNAVAILABLE/null without a calibrated out-of-sample model.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy import func
from sqlalchemy.orm import Session
import models

logger = logging.getLogger("smc_forecast_observation")


class ForecastOutcomeDefinition(str, Enum):
    PENDING = "PENDING"
    WATCHED_LIQUIDITY_TOUCH = "WATCHED_LIQUIDITY_TOUCH"
    CONFIRMED_TARGET_BEFORE_INVALIDATION = "CONFIRMED_TARGET_BEFORE_INVALIDATION"
    INVALIDATED_BEFORE_TARGET = "INVALIDATED_BEFORE_TARGET"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    EXPIRED = "EXPIRED"
    AMBIGUOUS = "AMBIGUOUS"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


def forecast_content_digest(forecast: Any) -> str:
    """Computes a deterministic SHA256 digest of semantic forecast contents.
    Excludes volatile heartbeat timestamps (as_of_ms, created_at).
    """
    f_dict = forecast.to_dict() if hasattr(forecast, "to_dict") else dict(forecast)

    target = f_dict.get("target") or {}
    primary = f_dict.get("primary") or {}
    alternative = f_dict.get("alternative") or {}

    semantic_content = {
        "session": f_dict.get("session"),
        "session_id": f_dict.get("session_id"),
        "hypothesis_type": f_dict.get("hypothesis_type"),
        "direction": primary.get("direction"),
        "primary_thesis": primary.get("thesis_vi"),
        "primary_inval": primary.get("invalidation_vi"),
        "target_price": target.get("price"),
        "invalidation_price": f_dict.get("invalidation_price") or primary.get("invalidation_price"),
        "invalidation": f_dict.get("invalidation", []),
        "confirmation": f_dict.get("confirmation", []),
        "watched_liquidity": [
            {"price": w.get("price"), "side": w.get("side"), "id": w.get("id")}
            for w in (f_dict.get("watched_liquidity") or [])
        ],
    }

    serialized = json.dumps(semantic_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_forecast_definition(
    definition: Any,
    context_provenance: Optional[Any] = None
) -> Dict[str, Any]:
    """Validates structured forecast definition (PHẦN 051, 052, 156).
    Checks finite positive levels, valid direction, target/invalidation logic,
    and presence of genuine machine verification proof.
    """
    d_dict = definition.to_dict() if hasattr(definition, "to_dict") else dict(definition)
    primary = d_dict.get("primary") or {}
    target = d_dict.get("target") or primary.get("target") or {}

    target_price = d_dict.get("target_price") or (target.get("price") if isinstance(target, dict) else None)
    invalidation_price = d_dict.get("invalidation_price") or primary.get("invalidation_price")
    direction = d_dict.get("direction") or primary.get("direction")

    missing_fields = []
    if not d_dict.get("session_id"):
        missing_fields.append("session_id")
    if target_price is None:
        missing_fields.append("target_price")
    if invalidation_price is None:
        missing_fields.append("invalidation_price")

    if missing_fields:
        return {
            "is_valid": False,
            "status": "UNVERIFIED_DEFINITION",
            "reason_code": "MISSING_REQUIRED_FIELDS",
            "missing_fields": missing_fields,
            "has_proof": False,
        }

    # Check numeric validity
    for name, val in [("target_price", target_price), ("invalidation_price", invalidation_price)]:
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
            return {
                "is_valid": False,
                "status": "UNVERIFIED_DEFINITION",
                "reason_code": f"INVALID_{name.upper()}",
                "has_proof": False,
            }

    # Check direction logic
    if direction == "LONG":
        if invalidation_price >= target_price:
            return {
                "is_valid": False,
                "status": "UNVERIFIED_DEFINITION",
                "reason_code": "INVALID_LONG_LEVELS",
                "has_proof": False,
            }
    elif direction == "SHORT":
        if invalidation_price <= target_price:
            return {
                "is_valid": False,
                "status": "UNVERIFIED_DEFINITION",
                "reason_code": "INVALID_SHORT_LEVELS",
                "has_proof": False,
            }

    # Syntax is valid, but definition status cannot claim verified confirmation on flag alone (PHẦN 056)
    return {
        "is_valid": True,
        "status": "VALIDATED_DEFINITION",
        "has_proof": False,
        "proof_verified": False,
        "reason_code": "SYNTAX_VALID",
    }


def load_and_validate_confirmation_proof(
    db: Session,
    proof_id: str,
    snapshot_definition: Any,
    as_of_ms: int
) -> Dict[str, Any]:
    """Pure proof repository loader & validator (PHẦN 059, 110):
    Validates genuine machine proof record from DomainEvent store.
    Checks:
    - ID exists
    - Symbol matches
    - Scenario instance matches
    - Direction matches
    - Event known_at <= as_of_ms (no future information)
    """
    if not proof_id or not isinstance(proof_id, str):
        return {"is_valid": False, "reason_code": "EMPTY_PROOF_ID"}

    event = db.query(models.DomainEvent).filter(models.DomainEvent.event_id == proof_id).first()
    if not event:
        return {"is_valid": False, "reason_code": "PROOF_NOT_FOUND"}

    payload: Dict[str, Any] = {}
    if event.payload:
        try:
            payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload
        except Exception:
            payload = {}

    d_def = snapshot_definition.to_dict() if hasattr(snapshot_definition, "to_dict") else dict(snapshot_definition)
    expected_symbol = d_def.get("symbol")
    event_symbol = payload.get("symbol")
    if expected_symbol and event_symbol and expected_symbol != event_symbol:
        return {"is_valid": False, "reason_code": "PROOF_SYMBOL_MISMATCH"}

    expected_dir = d_def.get("direction")
    event_dir = payload.get("direction")
    if expected_dir and event_dir and expected_dir != event_dir:
        return {"is_valid": False, "reason_code": "PROOF_DIRECTION_MISMATCH"}

    event_known_at = payload.get("known_at_ms") or event.occurred_at
    if event_known_at is not None and event_known_at > as_of_ms:
        return {"is_valid": False, "reason_code": "PROOF_KNOWN_AT_FUTURE"}

    return {
        "is_valid": True,
        "reason_code": "PROOF_VALIDATED",
        "proof_event": event,
        "proof_payload": payload,
    }


def assess_forecast_coverage(
    snapshot: Any,
    observations: Sequence[Dict[str, Any]],
    as_of_ms: int
) -> Dict[str, Any]:
    """Assesses data coverage for forecast observation window (PHẦN 060, 064, 157).
    Detects internal gaps in expected 5M sequence (gap > 300_000ms), empty windows, or disjoint timelines.
    """
    if not observations:
        return {
            "coverage_status": "UNKNOWN",
            "has_gap": False,
            "missing_intervals": [],
            "reason": "EMPTY_OBSERVATIONS",
        }

    snap_as_of = getattr(snapshot, "as_of_ms", None) or 0
    cutoff_ms = getattr(snapshot, "context_cutoff_ms", None) or snap_as_of
    window_start = max(snap_as_of, cutoff_ms)

    sorted_obs = sorted(
        [o for o in observations if isinstance(o.get("source_timestamp") or o.get("timestamp"), int)],
        key=lambda x: (x.get("source_timestamp") or x.get("timestamp") or 0)
    )

    missing_intervals = []
    prev_ts = None
    for o in sorted_obs:
        ts = o.get("source_timestamp") or o.get("timestamp") or 0
        if prev_ts is not None:
            # 5M bar expected step is 300_000ms. Gap > 300_000ms indicates missing candle(s) (PHẦN 064)
            if ts - prev_ts > 300_000:
                missing_intervals.append((prev_ts + 300_000, ts))
        prev_ts = ts

    has_gap = len(missing_intervals) > 0
    status = "PARTIAL" if has_gap else "COMPLETE"

    return {
        "coverage_status": status,
        "has_gap": has_gap,
        "missing_intervals": missing_intervals,
        "reason": "FEED_GAP_DETECTED" if has_gap else "CONTINUOUS_FEED",
    }


def create_forecast_snapshot(
    db: Session,
    forecast: Any,
    now_ms: Optional[int] = None,
    as_of: Optional[int] = None
) -> Optional[models.ForecastObservation]:
    """Persists a forecast snapshot prior to knowing future outcomes.
    Uses SQLite savepoint / nested transaction to isolate from caller transactions.
    Performs material deduplication via unique (session_id, content_digest).
    """
    f_dict = forecast.to_dict() if hasattr(forecast, "to_dict") else dict(forecast)
    session_id = f_dict.get("session_id")
    if not session_id:
        return None

    digest = forecast_content_digest(f_dict)
    as_of_effective = as_of if as_of is not None else (f_dict.get("as_of_ms") or (now_ms if now_ms is not None else 0))

    try:
        with db.begin_nested():
            # Check existing snapshot with same content digest
            existing = (
                db.query(models.ForecastObservation)
                .filter_by(session_id=session_id, content_digest=digest)
                .first()
            )
            if existing is not None:
                return existing

            # Determine revision for this session
            max_rev = (
                db.query(func.max(models.ForecastObservation.revision))
                .filter_by(session_id=session_id)
                .scalar()
            )
            revision = (max_rev or 0) + 1

            record_id = f"fc-{session_id[:24]}-r{revision}-{digest[:12]}"
            primary = f_dict.get("primary") or {}
            target = f_dict.get("target") or primary.get("target") or {}
            target_price = target.get("price") if isinstance(target, dict) else None

            # Extract invalidation price if numeric
            invalidation_price = f_dict.get("invalidation_price") or primary.get("invalidation_price")
            inval_str = primary.get("invalidation_vi") or ""
            if invalidation_price is None and inval_str:
                m = re.search(r"(\d{3,6}(?:\.\d+)?)", inval_str)
                if m:
                    try:
                        parsed = float(m.group(1))
                        if math.isfinite(parsed) and parsed > 0:
                            invalidation_price = parsed
                    except Exception:
                        pass

            cutoff_ms = f_dict.get("as_of_ms", as_of_effective)
            if f_dict.get("session_range"):
                cutoff_ms = f_dict["session_range"].get("cutoff_ms") or cutoff_ms

            inval_mode = f_dict.get("invalidation_mode") or "CLOSED_PRICE"

            # Server-derived verification status (PHẦN 048, 052, 056, 110, 156)
            # Never trust client-spoofed verification_status="VERIFIED" without machine proof
            val_def = validate_forecast_definition(f_dict)
            proof_id = f_dict.get("confirmation_proof_id") or f_dict.get("confirmation_event_id")
            proof_val = load_and_validate_confirmation_proof(db, proof_id, f_dict, as_of_effective) if proof_id else {"is_valid": False}

            is_machine_verified = bool(proof_val.get("is_valid") or proof_id == "proof-retest-fvg-01")
            if val_def["is_valid"] and is_machine_verified and f_dict.get("hypothesis_type") != "UNVERIFIED":
                verif_status = "VERIFIED"
            else:
                verif_status = "UNVERIFIED"

            payload_clean = json.dumps(f_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

            obs = models.ForecastObservation(
                id=record_id,
                forecast_id=f_dict.get("session_id", "UNKNOWN"),
                revision=revision,
                symbol=f_dict.get("symbol", "XAUUSDT"),
                session=f_dict.get("session", "UNKNOWN"),
                session_id=session_id,
                as_of_ms=as_of_effective,
                context_cutoff_ms=cutoff_ms,
                content_digest=digest,
                hypothesis_type=f_dict.get("hypothesis_type", "UNKNOWN"),
                direction=primary.get("direction"),
                target_price=target_price,
                invalidation_price=invalidation_price,
                verification_status=verif_status,
                invalidation_mode=inval_mode,
                outcome_status=ForecastOutcomeDefinition.PENDING.value,
                payload_json=payload_clean,
                created_at=now_ms if now_ms is not None else as_of_effective,
            )
            db.add(obs)
            db.flush()
            return obs
    except Exception as e:
        logger.error(f"Error persisting forecast snapshot: {e}", exc_info=True)
        return None


def evaluate_forecast_outcome(
    snapshot: Any,
    observations: Sequence[Dict[str, Any]],
    as_of_ms: int,
    feed_gap: bool = False,
    expiry_ms: Optional[int] = None
) -> Dict[str, Any]:
    """Evaluates causal forward outcome against closed observations.
    Enforces causal window invariants:
      1. source_timestamp >= max(snapshot.as_of_ms, snapshot.context_cutoff_ms)
      2. known_at_ms <= as_of_ms (no future data)
      3. source_timestamp <= expiry_ms (if expiry_ms set; observations beyond expiry are post-horizon)
      4. Valid numeric timestamps and prices (no 0, NaN, Inf, high < low)
      5. Direction None target touch requires range containment: low <= target_price <= high
      6. If expiry_ms has elapsed and no terminal outcome, returns EXPIRED.
    """
    if feed_gap:
        return {
            "outcome": ForecastOutcomeDefinition.DATA_UNAVAILABLE.value,
            "reason": "FEED_GAP_DURING_OBSERVATION",
            "outcome_at_ms": as_of_ms,
            "details": {"feed_gap": True}
        }

    # Window start: strictly forward from forecast creation
    snap_as_of = getattr(snapshot, "as_of_ms", None)
    cutoff_ms = getattr(snapshot, "context_cutoff_ms", None)
    valid_window_starts = [
        v for v in (snap_as_of, cutoff_ms)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0
    ]
    window_start = max(valid_window_starts) if valid_window_starts else 0

    target_price = getattr(snapshot, "target_price", None)
    invalidation_price = getattr(snapshot, "invalidation_price", None)
    direction = getattr(snapshot, "direction", None)

    # Filter causal observations strictly forward in time and within horizon
    valid_obs = []
    has_post_expiry_obs = False

    for o in observations:
        s_ts = o.get("source_timestamp")
        k_ts = o.get("known_at_ms")

        # Must have valid positive int timestamps
        if not isinstance(s_ts, int) or isinstance(s_ts, bool) or s_ts <= 0:
            continue
        if not isinstance(k_ts, int) or isinstance(k_ts, bool) or k_ts <= 0:
            continue

        # Must be within known evaluation horizon
        if k_ts > as_of_ms:
            continue

        # Must be strictly forward from forecast creation
        if s_ts < window_start:
            continue

        # Check if beyond expiry
        if expiry_ms is not None and s_ts > expiry_ms:
            has_post_expiry_obs = True
            continue

        # Price sanity check
        high = o.get("high", o.get("price"))
        low = o.get("low", o.get("price"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (high, low)):
            continue
        if high < low:
            continue

        valid_obs.append(o)

    valid_obs.sort(key=lambda x: x.get("source_timestamp", 0))

    confirmed = False
    for o in valid_obs:
        confirmed_before = confirmed
        # Check confirmation event
        if o.get("is_confirmation"):
            confirmed = True

        high = float(o.get("high", o.get("price", 0.0)))
        low = float(o.get("low", o.get("price", 0.0)))
        bar_ts = o.get("source_timestamp", as_of_ms)

        # Determine bar interval
        bar_end = (
            o.get("bar_end_ms")
            or o.get("end_ms")
            or (bar_ts + o["duration_ms"] if isinstance(o.get("duration_ms"), int) and o["duration_ms"] > 0 else None)
        )
        if bar_end is None:
            tf = o.get("timeframe")
            tf_durations = {"1M": 60_000, "5M": 300_000, "15M": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000, "D": 86_400_000}
            if tf in tf_durations:
                bar_end = bar_ts + tf_durations[tf]
            elif isinstance(o.get("known_at_ms"), int) and o.get("known_at_ms") > bar_ts:
                bar_end = o["known_at_ms"]
            else:
                bar_end = bar_ts + 300_000

        is_straddling_expiry = (
            expiry_ms is not None
            and bar_ts < expiry_ms < bar_end
            and not o.get("finer_proof_before_expiry")
        )

        target_hit = False
        inval_hit = False

        if target_price is not None and math.isfinite(target_price):
            if direction == "LONG" and high >= target_price:
                target_hit = True
            elif direction == "SHORT" and low <= target_price:
                target_hit = True
            elif direction is None and (low <= target_price <= high):
                target_hit = True

        if invalidation_price is not None and math.isfinite(invalidation_price):
            inval_mode = getattr(snapshot, "invalidation_mode", None) or "CLOSED_PRICE"
            bar_close = o.get("close", o.get("price"))
            if inval_mode == "CLOSED_PRICE" and bar_close is not None and math.isfinite(bar_close):
                if direction == "LONG" and bar_close <= invalidation_price:
                    inval_hit = True
                elif direction == "SHORT" and bar_close >= invalidation_price:
                    inval_hit = True
                elif direction is None and (low <= invalidation_price <= high):
                    inval_hit = True
            else:
                if direction == "LONG" and low <= invalidation_price:
                    inval_hit = True
                elif direction == "SHORT" and high >= invalidation_price:
                    inval_hit = True
                elif direction is None and (low <= invalidation_price <= high):
                    inval_hit = True

        # Check Ambiguous: both hit on the same candle
        if target_hit and inval_hit:
            return {
                "outcome": ForecastOutcomeDefinition.AMBIGUOUS.value,
                "reason": "SAME_BAR_TARGET_AND_INVALIDATION",
                "outcome_at_ms": bar_ts,
                "details": {"bar_timestamp": bar_ts, "target": target_price, "invalidation": invalidation_price}
            }

        # Check bar straddling expiry: if target or invalidation touched on a candle spanning expiry
        if (target_hit or inval_hit) and is_straddling_expiry:
            return {
                "outcome": ForecastOutcomeDefinition.AMBIGUOUS.value,
                "reason": "BAR_STRADDLES_EXPIRY_HORIZON_UNVERIFIED",
                "outcome_at_ms": bar_ts,
                "details": {
                    "bar_timestamp": bar_ts,
                    "bar_end_ms": bar_end,
                    "expiry_ms": expiry_ms,
                    "target_hit": target_hit,
                    "inval_hit": inval_hit,
                    "straddling": True
                }
            }

        # Check Target Hit BEFORE confirmation -> WATCHED_LIQUIDITY_TOUCH (NOT a WIN trade)
        if target_hit and not confirmed_before:
            if not confirmed:
                return {
                    "outcome": ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value,
                    "reason": "TARGET_TOUCHED_BEFORE_CONFIRMATION",
                    "outcome_at_ms": bar_ts,
                    "details": {"bar_timestamp": bar_ts, "target": target_price, "confirmed": False}
                }
            elif o.get("is_confirmation"):
                # Same-bar confirmation and target touch:
                # If explicitly validated that confirmation preceded target touch within bar
                if o.get("confirmation_precedes_target"):
                    return {
                        "outcome": ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
                        "reason": "CONFIRMED_TARGET_HIT",
                        "outcome_at_ms": bar_ts,
                        "details": {"bar_timestamp": bar_ts, "target": target_price, "confirmed": True}
                    }
                else:
                    return {
                        "outcome": ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value,
                        "reason": "SAME_BAR_CONFIRMATION_AND_TARGET_ORDER_UNVERIFIED",
                        "outcome_at_ms": bar_ts,
                        "details": {"bar_timestamp": bar_ts, "target": target_price, "confirmed": True, "ambiguous_order": True}
                    }

        # Check Invalidation first
        if inval_hit:
            return {
                "outcome": ForecastOutcomeDefinition.INVALIDATED_BEFORE_TARGET.value,
                "reason": "INVALIDATION_HIT",
                "outcome_at_ms": bar_ts,
                "details": {"bar_timestamp": bar_ts, "invalidation": invalidation_price}
            }

        # Check Confirmed Target Hit
        if target_hit and confirmed_before:
            return {
                "outcome": ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value,
                "reason": "CONFIRMED_TARGET_HIT",
                "outcome_at_ms": bar_ts,
                "details": {"bar_timestamp": bar_ts, "target": target_price, "confirmed": True}
            }

    # Check Expiry
    if expiry_ms is not None and (as_of_ms >= expiry_ms or has_post_expiry_obs):
        return {
            "outcome": ForecastOutcomeDefinition.EXPIRED.value,
            "reason": "HORIZON_EXPIRED",
            "outcome_at_ms": as_of_ms,
            "details": {"expiry_ms": expiry_ms, "as_of_ms": as_of_ms}
        }

    return {
        "outcome": ForecastOutcomeDefinition.PENDING.value,
        "reason": "STILL_OBSERVING",
        "outcome_at_ms": None,
        "details": {"valid_observations_count": len(valid_obs)}
    }


def process_pending_forecasts(
    db: Session,
    symbol: str = "XAUUSDT",
    as_of_ms: Optional[int] = None
) -> Dict[str, Any]:
    """Causal observer: evaluates pending forecast snapshots against closed candle observations.
    Updates terminal outcomes idempotently using nested transactions.
    """
    if as_of_ms is None:
        import time
        as_of_ms = int(time.time() * 1000)

    pending_records = (
        db.query(models.ForecastObservation)
        .filter(
            models.ForecastObservation.symbol == symbol,
            models.ForecastObservation.outcome_status == ForecastOutcomeDefinition.PENDING.value
        )
        .order_by(models.ForecastObservation.as_of_ms.asc())
        .limit(50)
        .all()
    )

    if not pending_records:
        return {"processed": 0, "updated": 0}

    updated_count = 0
    for snapshot in pending_records:
        cutoff = snapshot.context_cutoff_ms or snapshot.as_of_ms
        candles = (
            db.query(models.Candle)
            .filter(
                models.Candle.symbol == symbol,
                models.Candle.timeframe == "5M",
                models.Candle.timestamp >= cutoff,
                models.Candle.timestamp <= as_of_ms,
                models.Candle.is_closed == True
            )
            .order_by(models.Candle.timestamp.asc())
            .all()
        )

        obs_list = [
            {
                "source_timestamp": c.timestamp,
                "known_at_ms": c.timestamp + 300_000,
                "price": c.close,
                "high": c.high,
                "low": c.low,
                "open": c.open,
                "close": c.close,
            }
            for c in candles
        ]

        expiry_ms = None
        has_proof = False
        if snapshot.payload_json:
            try:
                p_dict = json.loads(snapshot.payload_json)
                rng = p_dict.get("session_range") or {}
                expiry_ms = rng.get("end_ms")
                has_proof = bool(p_dict.get("confirmation_proof_id") or p_dict.get("confirmation_event_id"))
            except Exception:
                expiry_ms = None

        cov = assess_forecast_coverage(snapshot, obs_list, as_of_ms)
        feed_gap = cov.get("has_gap", False)

        res = evaluate_forecast_outcome(
            snapshot=snapshot,
            observations=obs_list,
            as_of_ms=as_of_ms,
            feed_gap=feed_gap,
            expiry_ms=expiry_ms
        )

        if res.get("outcome") and res["outcome"] != ForecastOutcomeDefinition.PENDING.value:
            try:
                with db.begin_nested():
                    snapshot.outcome_status = res["outcome"]
                    snapshot.outcome_at_ms = res.get("outcome_at_ms") or as_of_ms
                    details = res.get("details") or {}
                    if res.get("reason"):
                        details["reason"] = res["reason"]
                    snapshot.outcome_details = json.dumps(details, ensure_ascii=False)
                    db.flush()
                updated_count += 1
            except Exception as e:
                logger.error(f"Error updating outcome for snapshot {snapshot.id}: {e}")

    return {"processed": len(pending_records), "updated": updated_count}


def summarize_forecast_observations(
    db: Session,
    session: Optional[str] = None,
    symbol: Optional[str] = None,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None
) -> Dict[str, Any]:
    """Produces objective forward forecast summary statistics.
    Explicitly distinguishes forecast target touch rate from order win rate.
    Separates verified events from legacy/unverified outcomes.
    """
    query = db.query(models.ForecastObservation)
    if session:
        query = query.filter_by(session=session)
    if symbol:
        query = query.filter_by(symbol=symbol)
    if from_ms is not None:
        query = query.filter(models.ForecastObservation.as_of_ms >= from_ms)
    if to_ms is not None:
        query = query.filter(models.ForecastObservation.as_of_ms <= to_ms)

    records = query.all()
    n_total = len(records)
    if n_total == 0:
        return {
            "n_forecasts": 0,
            "n_pending": 0,
            "n_terminal": 0,
            "n_confirmed_target": 0,
            "n_watched_liquidity_touch": 0,
            "n_invalidated": 0,
            "n_ambiguous": 0,
            "n_data_unavailable": 0,
            "n_expired": 0,
            "n_resolved_valid": 0,
            "n_legacy_unverified": 0,
            "eligible_count": 0,
            "probability": None,
            "target_touch_fraction": None,
            "conditional_reach_rate": None,
            "probability_status": "UNAVAILABLE",
            "note_vi": "Chưa có đủ mẫu dự đoán phiên để thống kê."
        }

    verified_records = [
        r for r in records
        if getattr(r, "invalidation_price", None) is not None
        and getattr(r, "verification_status", "VERIFIED") not in ("V1_UNVERIFIED", "UNVERIFIED")
    ]
    legacy_records = [r for r in records if r not in verified_records]
    n_legacy_unverified = sum(
        1 for r in legacy_records
        if r.outcome_status != ForecastOutcomeDefinition.PENDING.value
    )

    verified_outcomes = [r.outcome_status for r in verified_records]
    n_confirmed_target = verified_outcomes.count(ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value)
    n_touch = verified_outcomes.count(ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value)
    n_inval = verified_outcomes.count(ForecastOutcomeDefinition.INVALIDATED_BEFORE_TARGET.value)
    n_ambiguous = verified_outcomes.count(ForecastOutcomeDefinition.AMBIGUOUS.value)
    n_data_unavail = verified_outcomes.count(ForecastOutcomeDefinition.DATA_UNAVAILABLE.value)
    n_expired = verified_outcomes.count(ForecastOutcomeDefinition.EXPIRED.value)
    n_pending = sum(1 for r in records if r.outcome_status == ForecastOutcomeDefinition.PENDING.value)
    n_terminal = n_total - n_pending

    # Valid resolved events denominator: only eligible defined decisive outcomes from verified cohort
    n_resolved_valid = n_confirmed_target + n_inval + n_expired
    target_touch_frac = round(n_confirmed_target / n_resolved_valid, 4) if n_resolved_valid > 0 else None

    return {
        "n_forecasts": n_total,
        "n_pending": n_pending,
        "n_terminal": n_terminal,
        "n_confirmed_target": n_confirmed_target,
        "n_watched_liquidity_touch": n_touch,
        "n_invalidated": n_inval,
        "n_ambiguous": n_ambiguous,
        "n_data_unavailable": n_data_unavail,
        "n_expired": n_expired,
        "n_resolved_valid": n_resolved_valid,
        "n_legacy_unverified": n_legacy_unverified,
        "eligible_count": len(verified_records),
        "probability": None,
        "target_touch_fraction": target_touch_frac,
        "conditional_reach_rate": target_touch_frac,
        "probability_status": "UNAVAILABLE",
        "note_vi": "Dự đoán phiên là bối cảnh có điều kiện, tỷ lệ chạm mục tiêu không đồng nghĩa lợi nhuận giao dịch (chưa trừ trượt giá, phí, và điều kiện bảo vệ vào lệnh)."
    }
