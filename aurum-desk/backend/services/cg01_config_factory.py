"""
backend/services/cg01_config_factory.py
V16.21 Canonical Shared Effective CG-01 Config Factory (Rules 016, 017, 023, 024, 046-065).

Provides a single authoritative factory for resolving effective CG01Config,
ensuring strict parity between ACTIVE orchestrator and SHADOW scheduler.
Enforces precedence:
1. Explicit replay / test override (base_config or cfg.cg01_config).
2. Persisted / effective scalp profile (load_effective_scalp_config + resolve_effective_daily_policy).
3. Canonical defaults.

Computes a deterministic content fingerprint across all decision-affecting fields
actually present in CG01Config (no fabricated fields).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from services.cg01_types import CG01Config, CG01ProfileMode

logger = logging.getLogger("cg01_config_factory")


def compute_cg01_config_fingerprint(cfg: CG01Config) -> str:
    """
    Rules 052, 053, 056, 057: Deterministic content fingerprint across all
    fields of CG01Config that actually affect evaluation decisions.
    """
    payload = {
        "model_version": cfg.model_version,
        "config_version": cfg.config_version,
        "profile_mode": cfg.profile_mode,
        "enabled": cfg.enabled,
        "entry_mode": cfg.entry_mode,
        "timing_profile": cfg.timing_profile,
        "entry_timeframe": cfg.entry_timeframe,
        "min_net_rr": cfg.min_net_rr,
        "displacement_body_atr_min": cfg.displacement_body_atr_min,
        "displacement_atr_period": cfg.displacement_atr_period,
        "pivot_left_bars": cfg.pivot_left_bars,
        "pivot_right_bars": cfg.pivot_right_bars,
        "sl_buffer_ticks": cfg.sl_buffer_ticks,
        "tick_size": cfg.tick_size,
        "htf_min_bars": cfg.htf_min_bars,
        "htf_break_buffer_ticks": cfg.htf_break_buffer_ticks,
        "max_touch_episodes": cfg.max_touch_episodes,
        "max_fvg_mitigation_ratio": cfg.max_fvg_mitigation_ratio,
        "max_trigger_age_ms": cfg.max_trigger_age_ms,
        "candidate_ttl_ms": cfg.candidate_ttl_ms,
        "fallback_risk_pct": cfg.fallback_risk_pct,
        "daily_min_goal": cfg.daily_min_goal,
        "daily_max_cap": cfg.daily_max_cap,
        "daily_ny_goal": cfg.daily_ny_goal,
        "ny_timezone": cfg.ny_timezone,
        "direction_policy": getattr(cfg, "direction_policy", "LEGACY_H1"),
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def resolve_canonical_cg01_config(
    db: Optional[Session] = None,
    symbol: str = "XAUUSDT",
    base_config: Optional[CG01Config] = None,
    scalp_config_override: Optional[Any] = None,
    explicit_profile_mode: Optional[str] = None,
    is_explicit_override: Optional[bool] = None,
) -> Tuple[CG01Config, str, str]:
    """
    Rules 046-065, V16.22 Rules 071-085: Authoritative shared factory for effective CG01Config.
    Used by BOTH scalp_watcher_orchestrator (ACTIVE) and cg01_scheduler_service (SHADOW).

    Returns:
        (config: CG01Config, fingerprint: str, source: str)
    """
    # 1. Explicit override precedence (Rule 048, 049)
    if base_config is not None and isinstance(base_config, CG01Config):
        fp = compute_cg01_config_fingerprint(base_config)
        return base_config, fp, "EXPLICIT_PARAMETER"

    if scalp_config_override is not None:
        explicit_cg01 = getattr(scalp_config_override, "cg01_config", None)
        if explicit_cg01 is not None and isinstance(explicit_cg01, CG01Config):
            fp = compute_cg01_config_fingerprint(explicit_cg01)
            return explicit_cg01, fp, "EXPLICIT_SCALP_OVERRIDE"

    # 2. Persisted / effective profile precedence (Rule 048)
    from services.scalp_m1_types import load_effective_scalp_config
    scalp_cfg = scalp_config_override or load_effective_scalp_config(db)

    # Resolve profile mode and enabled status (Rules 050, 051, V16.38 Rules 029, 030)
    exec_profile = getattr(scalp_cfg, "execution_profile", "BASELINE")
    cg01_enabled = bool(getattr(scalp_cfg, "cg01_enabled", False))
    resolved_profile_mode = explicit_profile_mode
    if resolved_profile_mode is None:
        resolved_profile_mode = getattr(scalp_cfg, "cg01_profile_mode", None)

    if exec_profile == "PAPER_ADAPTIVE_SMC":
        allowlist = getattr(scalp_cfg, "adaptive_model_allowlist", ())
        cg01_in_allowlist = "CG01_SWEEP_MSS_FVG" in allowlist
        if not cg01_in_allowlist:
            cg01_enabled = False
            resolved_profile_mode = CG01ProfileMode.OFF.value
        else:
            scalp_mode = getattr(scalp_cfg, "scalp_m1_mode", "OFF")
            if scalp_mode == "PAPER":
                cg01_enabled = True
                resolved_profile_mode = CG01ProfileMode.PAPER_EXECUTION.value
            elif scalp_mode == "SHADOW":
                cg01_enabled = True
                resolved_profile_mode = CG01ProfileMode.SHADOW.value
            else:
                cg01_enabled = False
                resolved_profile_mode = CG01ProfileMode.OFF.value
    else:
        if resolved_profile_mode is None:
            resolved_profile_mode = CG01ProfileMode.PAPER_EXECUTION.value if cg01_enabled else CG01ProfileMode.OFF.value

    # Extract strategy parameters from scalp_cfg
    min_net_rr = getattr(scalp_cfg, "min_net_rr", None)
    if min_net_rr is None and db is not None:
        try:
            from services.rr_policy_service import resolve_effective_rr_policy
            rr_ctx = resolve_effective_rr_policy(db)
            min_net_rr = rr_ctx.min_net_rr
        except Exception as e:
            logger.debug(f"CG01 min_net_rr resolution from DB skipped: {e}")
            min_net_rr = 2.0
    min_net_rr = float(min_net_rr if min_net_rr is not None else 2.0)
    displacement_body_atr_min = float(getattr(scalp_cfg, "displacement_body_atr_min", 0.80))
    daily_min_goal = int(getattr(scalp_cfg, "daily_min_goal", 2))
    daily_max_cap = int(getattr(scalp_cfg, "daily_max_cap", 3))
    daily_ny_goal = int(getattr(scalp_cfg, "daily_ny_goal", 1))

    source = "SCALP_CONFIG"

    # Daily policy integration (Rule 054, V16.22 Rules 071-085)
    # When caller does not specify an explicit override, integrate authoritative daily policy from DB
    should_integrate_daily_policy = (not is_explicit_override) if is_explicit_override is not None else (scalp_config_override is None)
    if should_integrate_daily_policy and db is not None:
        try:
            from services.trading_policy_service import resolve_effective_daily_policy
            eff_daily = resolve_effective_daily_policy(db, "default")
            if eff_daily:
                daily_min_goal = eff_daily.min_goal
                daily_max_cap = eff_daily.max_fills
                daily_ny_goal = eff_daily.ny_goal
                source = "DATABASE_POLICY"
        except Exception as e:
            logger.debug(f"Daily policy resolution skipped: {e}")
            source = "DATABASE_CONFIG"

    # Build final canonical CG01Config (Rule 050: enabled strictly reflects config)
    final_enabled = (resolved_profile_mode != CG01ProfileMode.OFF.value) and cg01_enabled

    cfg = CG01Config(
        enabled=final_enabled,
        profile_mode=resolved_profile_mode,
        min_net_rr=min_net_rr,
        displacement_body_atr_min=displacement_body_atr_min,
        daily_min_goal=daily_min_goal,
        daily_max_cap=daily_max_cap,
        daily_ny_goal=daily_ny_goal,
        pivot_left_bars=int(getattr(scalp_cfg, "pivot_left_bars", 2)),
        pivot_right_bars=int(getattr(scalp_cfg, "pivot_right_bars", 2)),
        timing_profile=str(getattr(scalp_cfg, "timing_profile", "SILVER_BULLET_LONDON_NY")),
        entry_mode=str(getattr(scalp_cfg, "entry_mode", "LIMIT_AT_CE_AFTER_CONFIRMATION")),
        sl_buffer_ticks=int(getattr(scalp_cfg, "sl_buffer_ticks", 5)),
        tick_size=float(getattr(scalp_cfg, "tick_size", 0.01)),
        htf_min_bars=int(getattr(scalp_cfg, "htf_min_bars", 12)),
        htf_break_buffer_ticks=int(getattr(scalp_cfg, "htf_break_buffer_ticks", 0)),
        max_touch_episodes=int(getattr(scalp_cfg, "max_touch_episodes", 2)),
        max_fvg_mitigation_ratio=float(getattr(scalp_cfg, "max_fvg_mitigation_ratio", 0.50)),
        max_trigger_age_ms=int(getattr(scalp_cfg, "max_trigger_age_ms", 600_000)),
        candidate_ttl_ms=int(getattr(scalp_cfg, "candidate_ttl_ms", 300_000)),
        fallback_risk_pct=float(getattr(scalp_cfg, "fallback_risk_pct", 0.10)),
        ny_timezone=str(getattr(scalp_cfg, "ny_timezone", "America/New_York")),
        direction_policy=str(getattr(scalp_cfg, "direction_policy", getattr(scalp_cfg, "cg01_direction_policy", "LEGACY_H1"))),
    )

    fingerprint = compute_cg01_config_fingerprint(cfg)
    return cfg, fingerprint, source
