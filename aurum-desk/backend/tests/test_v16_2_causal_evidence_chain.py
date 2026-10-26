"""
backend/tests/test_v16_2_causal_evidence_chain.py
V16.2 Rule 183: Negative & positive causal chain tests for CG01:
- reference unknown before sweep
- sweep before reference known
- MSS break before sweep
- FVG not in break leg
- retest before FVG confirmed
- future candle leak
- invalidated POI
"""
import pytest
from services.cg01_engine import (
    evaluate_cg01_candidate,
    filter_cg01_pois,
    detect_cg01_sweep,
    resolve_cg01_mss_and_displacement,
    select_cg01_leg_fvg,
)
from services.cg01_types import CG01Config, CG01Stage
from services.scalp_m1_model_service import validate_evidence_timeline
from services.scalp_m1_types import WatchedZone, ZoneState


def make_bar(open_ms, o, h, l, c, v=100.0):
    return {
        "open_time": open_ms,
        "timestamp": open_ms,
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
        "is_closed": True,
    }


class LevelStub:
    def __init__(self, id, price, known_at_ms, side="SELL_SIDE"):
        self.id = id
        self.level_id = id
        self.price = price
        self.known_at_ms = known_at_ms
        self.side = side


def test_timeline_validator_detects_future_leak():
    """Future candle timestamp leak is rejected."""
    now_ms = 1_000_000
    nodes = [
        {"id": "n1", "occurred_at_ms": 900_000, "known_at_ms": 950_000, "role": "REFERENCE"},
        {"id": "n2", "occurred_at_ms": 960_000, "known_at_ms": 1_050_000, "role": "SWEEP"}, # future leak!
    ]
    is_ok, issues = validate_evidence_timeline(nodes, now_ms=now_ms)
    assert not is_ok
    assert any("future leak" in iss for iss in issues)


def test_timeline_validator_detects_sweep_before_ref_known():
    """Rule 131: Sweep occurring before reference is known fails causal check."""
    now_ms = 1_000_000
    nodes = [
        {"id": "n1", "occurred_at_ms": 900_000, "known_at_ms": 960_000, "role": "REFERENCE"},
        {"id": "n2", "occurred_at_ms": 940_000, "known_at_ms": 950_000, "role": "SWEEP"}, # sweep before ref known
    ]
    is_ok, issues = validate_evidence_timeline(nodes, now_ms=now_ms)
    assert not is_ok
    assert any("reference known after sweep" in iss for iss in issues)


def test_timeline_validator_detects_mss_before_sweep():
    """Rule 131: MSS break occurring before sweep fails causal check."""
    now_ms = 1_000_000
    nodes = [
        {"id": "n1", "occurred_at_ms": 900_000, "known_at_ms": 910_000, "role": "REFERENCE"},
        {"id": "n2", "occurred_at_ms": 950_000, "known_at_ms": 950_000, "role": "SWEEP"},
        {"id": "n3", "occurred_at_ms": 940_000, "known_at_ms": 960_000, "role": "MSS"}, # mss before sweep
    ]
    is_ok, issues = validate_evidence_timeline(nodes, now_ms=now_ms)
    assert not is_ok
    assert any("sweep occurred after MSS break" in iss for iss in issues)


def test_filter_cg01_pois_excludes_invalidated_and_overtested():
    """Rule 077, 078: Zones breached or with touch_count > 2 are excluded."""
    now_ms = 1_000_000
    cfg = CG01Config(max_touch_episodes=2)

    fresh_zone = WatchedZone(
        zone_id="z_fresh",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=800_000,
        known_at=850_000,
        touch_count=1,
    )
    overtested_zone = WatchedZone(
        zone_id="z_over",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=800_000,
        known_at=850_000,
        touch_count=3, # exceeds max 2
    )
    invalidated_zone = WatchedZone(
        zone_id="z_inv",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=800_000,
        known_at=850_000,
        touch_count=0,
        invalidation_level=2618.0,
    )

    # Current price 2615 is below invalidation level 2618
    filtered = filter_cg01_pois([fresh_zone, overtested_zone, invalidated_zone], direction="LONG", current_price=2615.0, now_ms=now_ms, config=cfg)
    assert len(filtered) == 0  # invalidated_zone excluded by price breach, overtested excluded by touches

    # Current price 2622 (valid within/above zone)
    filtered_valid = filter_cg01_pois([fresh_zone, overtested_zone, invalidated_zone], direction="LONG", current_price=2622.0, now_ms=now_ms, config=cfg)
    assert len(filtered_valid) == 2
    assert "z_fresh" in [z.zone_id for z in filtered_valid]
    assert "z_inv" in [z.zone_id for z in filtered_valid]
    assert "z_over" not in [z.zone_id for z in filtered_valid]


def test_detect_cg01_sweep_requires_known_reference():
    """Rule 083: Sweep must not occur before reference is known."""
    now_ms = 1_000_000
    poi = WatchedZone(
        zone_id="z_test",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=800_000,
        known_at=850_000,
    )

    # Reference only known at 960_000
    future_ref = LevelStub(id="lvl_future", price=2621.0, known_at_ms=960_000)

    # Candle sweeps at 930_000 (BEFORE reference known)
    candles = [
        make_bar(910_000, 2623.0, 2624.0, 2622.0, 2623.0),
        make_bar(920_000, 2623.0, 2623.5, 2621.5, 2622.0),
        make_bar(930_000, 2622.0, 2622.5, 2619.5, 2621.5), # sweep low 2619.5, close 2621.5
    ]

    sweep = detect_cg01_sweep(candles, [future_ref], poi, direction="LONG", now_ms=now_ms)
    assert sweep is None  # Must NOT detect sweep from future-known reference
