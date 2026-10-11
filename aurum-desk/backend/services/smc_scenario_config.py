"""
backend/services/smc_scenario_config.py
V14.4 — Configuration and safety feature flags for Causal SMC Scenarios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ScenarioConfig:
    detector_version: str = "v14.4"
    config_version: int = 1
    family: str = "STANDARD_SMC"
    
    # Structural parameters
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    displacement_body_atr_min: float = 0.8   # Body >= 0.8 * ATR qualifies displacement (inherited from SMC engine)
    eqh_eql_tolerance_atr: float = 0.25       # Tolerance for equal highs/lows clustering
    fvg_min_size_atr: float = 0.15           # Minimum FVG zone thickness
    retest_buffer_atr: float = 0.15          # Buffer around FVG edge for touch confirmation
    sl_buffer_atr: float = 0.20              # Invalidation / SL buffer beyond sweep extreme
    min_net_rr: float = 2.0                  # Threshold for certified entry plan
    default_risk_pct: float = 0.25           # Standard 0.25% equity risk
    sl_buffer_ticks: int = 10                # 10 ticks SL buffer
    scenario_expiry_bars: int = 24           # 24 bars expiry
    
    # Expiry / TTL
    scenario_ttl_ms: int = 45 * 60 * 1000    # 45 minutes standard TTL
    
    # Feature flags (Phần 013)
    scenario_explain_enabled: bool = True    # Real-time explanation always active for PAPER observation
    certified_smc_entry_enabled: bool = True # Gate STANDARD_SMC entries with certified evidence chain


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
