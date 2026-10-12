"""
backend/services/smc_scenario_config.py
V14.4.1 — Configuration and safety feature flags for Causal SMC Scenarios.

All thresholds here are MODEL HYPOTHESES (config), not validated probabilities.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScenarioConfig:
    detector_version: str = "v14.4.1"
    config_version: int = 1
    family: str = "STANDARD_SMC"

    # Structural parameters
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    displacement_body_atr_min: float = 0.8   # Body >= 0.8 * prefix-ATR qualifies displacement
    displacement_atr_period: int = 14        # ATR lookback over closed bars strictly BEFORE the break candle
    min_atr_bars: int = 20                   # closed M5 bars required before any ATR-based decision
    eqh_eql_tolerance_atr: float = 0.25      # Tolerance for equal highs/lows clustering
    fvg_min_size_atr: float = 0.15           # Minimum FVG zone thickness
    retest_buffer_atr: float = 0.15          # Buffer around FVG edge for touch confirmation
    retest_validity_bars: int = 6            # A retest older than this many closed M5 bars is stale
    sl_buffer_atr: float = 0.20              # Invalidation / SL buffer beyond sweep extreme
    min_net_rr: float = 2.0                  # Threshold for certified entry plan
    default_risk_pct: float = 0.25           # Fallback only for tests/preview; production passes settings
    sl_buffer_ticks: int = 10                # SL buffer in ticks beyond the actual sweep wick extreme
    tick_size: float = 0.01                  # Price tick (instrument metadata)
    scenario_expiry_bars: int = 24           # Expiry in setup-timeframe bars

    # Data quality
    min_session_coverage_ratio: float = 0.80  # observed/expected bars for a session range to be trusted
    max_context_staleness_bars: int = 3       # last closed bar older than N bars => stale context
    require_m15_poi: bool = False             # True: M15 POI is mandatory; False: its absence is advisory

    # Expiry / TTL
    scenario_ttl_ms: int = 45 * 60 * 1000    # 45 minutes standard TTL

    # Feature flags
    scenario_explain_enabled: bool = True    # Scheduling of the market-reading cycle / UI explanation
    certified_smc_entry_enabled: bool = True # Gate STANDARD_SMC entries with the certified evidence chain

    def __post_init__(self) -> None:
        if not isinstance(self.displacement_atr_period, int) or self.displacement_atr_period < 2:
            raise ValueError("INVALID_CONFIG: displacement_atr_period must be an int >= 2")
        if not isinstance(self.min_atr_bars, int) or self.min_atr_bars <= self.displacement_atr_period:
            raise ValueError("INVALID_CONFIG: min_atr_bars must be greater than displacement_atr_period")
        if not 0.0 < self.min_session_coverage_ratio <= 1.0:
            raise ValueError("INVALID_CONFIG: min_session_coverage_ratio must be in (0, 1]")
        if self.tick_size <= 0:
            raise ValueError("INVALID_CONFIG: tick_size must be > 0")
        if self.displacement_body_atr_min <= 0:
            raise ValueError("INVALID_CONFIG: displacement_body_atr_min must be > 0")


# Global active config instance
active_scenario_config = ScenarioConfig(
    scenario_explain_enabled=os.environ.get("AURUM_SMC_EXPLAIN_ENABLED", "1") != "0",
    certified_smc_entry_enabled=os.environ.get("AURUM_SMC_CERTIFIED_ENTRY", "1") != "0",
)


def get_scenario_config(**overrides: Any) -> ScenarioConfig:
    if not overrides:
        return active_scenario_config
    base_dict = active_scenario_config.__dict__.copy()
    base_dict.update(overrides)
    return ScenarioConfig(**base_dict)


def describe_model_requirements(config: ScenarioConfig) -> dict[str, Any]:
    """
    Returns a pure descriptor of the current causal SMC requirements.
    This reflects the exact constraints enforced by the pipeline, not just cosmetic UI labels.
    """
    return {
        "sweep": {"importance": "REQUIRED", "description": "Liquidity sweep must occur at the anchor extreme."},
        "break": {"importance": "REQUIRED", "description": "Market structure must break (BOS/CHoCH) past a valid pivot."},
        "displacement": {
            "importance": "REQUIRED",
            "description": f"Break candle body must exceed {config.displacement_body_atr_min}x of {config.displacement_atr_period}-period ATR."
        },
        "fvg": {
            "importance": "REQUIRED",
            "description": f"A Fair Value Gap of at least {config.fvg_min_size_atr}x ATR must form along the displacement leg."
        },
        "retest": {
            "importance": "REQUIRED",
            "description": f"Price must retest the FVG within {config.retest_validity_bars} bars with a {config.retest_buffer_atr}x ATR buffer."
        },
        "target": {"importance": "REQUIRED", "description": "A valid unconsumed liquidity target must exist."},
        "m15_poi": {
            "importance": "REQUIRED" if config.require_m15_poi else "SUPPORTING",
            "description": "M15 Point of Interest alignment."
        },
        "htf_h1_semantics": {
            "description": "Counter-trend setups are permitted if the structural chain is exceptionally strong, but H4/H1 alignment adds confluence."
        },
        "config_version": config.config_version,
        "detector_version": config.detector_version,
        "family": config.family
    }
