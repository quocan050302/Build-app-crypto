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


def create_forecast_snapshot(
    db: Session,
    forecast: Any,
    now_ms: Optional[int] = None
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
    as_of = f_dict.get("as_of_ms") or (now_ms if now_ms is not None else 0)

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
            target = f_dict.get("target") or {}
            target_price = target.get("price")

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

            cutoff_ms = f_dict.get("as_of_ms", as_of)
            if f_dict.get("session_range"):
                cutoff_ms = f_dict["session_range"].get("cutoff_ms") or cutoff_ms

            payload_clean = json.dumps(f_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

            obs = models.ForecastObservation(
                id=record_id,
                forecast_id=f_dict.get("session_id", "UNKNOWN"),
                revision=revision,
                symbol=f_dict.get("symbol", "XAUUSDT"),
                session=f_dict.get("session", "UNKNOWN"),
                session_id=session_id,
                as_of_ms=as_of,
                context_cutoff_ms=cutoff_ms,
                content_digest=digest,
                hypothesis_type=f_dict.get("hypothesis_type", "UNKNOWN"),
                direction=primary.get("direction"),
                target_price=target_price,
                invalidation_price=invalidation_price,
                outcome_status=ForecastOutcomeDefinition.PENDING.value,
                payload_json=payload_clean,
                created_at=now_ms if now_ms is not None else as_of,
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
        if snapshot.payload_json:
            try:
                p_dict = json.loads(snapshot.payload_json)
                rng = p_dict.get("session_range") or {}
                expiry_ms = rng.get("end_ms")
            except Exception:
                expiry_ms = None

        res = evaluate_forecast_outcome(
            snapshot=snapshot,
            observations=obs_list,
            as_of_ms=as_of_ms,
            feed_gap=False,
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
            "target_touch_fraction": None,
            "conditional_reach_rate": None,
            "probability_status": "UNAVAILABLE",
            "note_vi": "Chưa có đủ mẫu dự đoán phiên để thống kê."
        }

    outcomes = [r.outcome_status for r in records]
    n_confirmed_target = outcomes.count(ForecastOutcomeDefinition.CONFIRMED_TARGET_BEFORE_INVALIDATION.value)
    n_touch = outcomes.count(ForecastOutcomeDefinition.WATCHED_LIQUIDITY_TOUCH.value)
    n_inval = outcomes.count(ForecastOutcomeDefinition.INVALIDATED_BEFORE_TARGET.value)
    n_ambiguous = outcomes.count(ForecastOutcomeDefinition.AMBIGUOUS.value)
    n_data_unavail = outcomes.count(ForecastOutcomeDefinition.DATA_UNAVAILABLE.value)
    n_expired = outcomes.count(ForecastOutcomeDefinition.EXPIRED.value)
    n_pending = outcomes.count(ForecastOutcomeDefinition.PENDING.value)
    n_terminal = n_total - n_pending

    # Distinguish legacy/unverified (records created without numeric invalidation)
    n_legacy_unverified = sum(
        1 for r in records
        if r.invalidation_price is None and r.outcome_status != ForecastOutcomeDefinition.PENDING.value
    )

    # Valid resolved events denominator: only eligible defined decisive outcomes
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
        "target_touch_fraction": target_touch_frac,
        "conditional_reach_rate": target_touch_frac,
        "probability_status": "UNAVAILABLE",
        "note_vi": "Dự đoán phiên là bối cảnh có điều kiện, tỷ lệ chạm mục tiêu không đồng nghĩa lợi nhuận giao dịch (chưa trừ trượt giá, phí, và điều kiện bảo vệ vào lệnh)."
    }
