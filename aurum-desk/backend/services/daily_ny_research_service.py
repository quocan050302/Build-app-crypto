"""
backend/services/daily_ny_research_service.py
V15.0 - Daily New York Research Paper Model (PHẦN 062 - 067).

Features:
- Separate research model label: NY_DAILY_RESEARCH (Never fakes STANDARD_SMC).
- Evaluates during active NY trading sessions to achieve 1 paper research trade/day when eligible.
- If a standard trade already filled today, research trade does NOT trigger (PHẦN 065).
- Strictly PAPER ONLY. Never forces fills on ineligible days (stale quotes, missing data, guard blocks).
- Shared max 3 fills/day limit across all models.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from domain_calculator import CalculationResult, calculate_risk_reward, validate_price_geometry
from services.ny_quota_helpers import closed_candles_at
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityReasonCode,
    get_scalp_config,
)
from services.smc_scenario_types import EvidenceNode, ScenarioSnapshot, ScenarioState

logger = logging.getLogger(__name__)


class DailyNYResearchService:
    @staticmethod
    def evaluate_daily_research_candidate(
        candles_5m: Sequence[Any],
        current_quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        today_fills_count: int,
        *,
        now_ms: int,
        is_ny_session: bool = True,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate daily NY paper research candidate (PHẦN 062, 063, 064).
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.NY_DAILY_RESEARCH.value
        candidate_key = f"{model_id}_{now_ms}"

        # 1. Daily fulfillment check (PHẦN 065)
        # If already filled a trade today (standard or research), do not open another research trade
        if today_fills_count >= 1:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": "DAILY_TARGET_ALREADY_MET",
                "missing_conditions": [f"Mục tiêu tối thiểu 1 lệnh/ngày đã đạt ({today_fills_count} lệnh đã khớp)"],
                "as_of_ms": now_ms,
            }

        # Shared daily limit check (max 3 fills/day)
        max_daily_trades = int(policy_settings.get("max_daily_trades", 3))
        if today_fills_count >= max_daily_trades:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.DAILY_LIMIT_REACHED.value,
                "missing_conditions": [f"Đã đạt giới hạn tối đa trong ngày ({today_fills_count}/{max_daily_trades})"],
                "as_of_ms": now_ms,
            }

        # 2. Session window check (PHẦN 061)
        if not is_ny_session:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.SESSION_CLOSED.value,
                "missing_conditions": ["Ngoài khung giờ giao dịch phiên New York"],
                "as_of_ms": now_ms,
            }

        # 3. Data readiness & quote validity (PHẦN 005, 064)
        try:
            closed_5m = closed_candles_at(candles_5m, now_ms=now_ms, duration_ms=300_000)
        except ValueError as err:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.DATA_INCOMPLETE.value,
                "missing_conditions": [f"Lỗi dữ liệu nến 5M: {err}"],
                "as_of_ms": now_ms,
            }
        if len(closed_5m) < 20:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.DATA_INCOMPLETE.value,
                "missing_conditions": [f"Thiếu nến 5M đã đóng: {len(closed_5m)}/20"],
                "as_of_ms": now_ms,
            }

        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        is_quote_valid = (
            bool(getattr(current_quote, "is_valid", True))
            and not bool(getattr(current_quote, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )

        if not is_quote_valid:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.QUOTE_STALE.value,
                "missing_conditions": ["Báo giá thị trường không hợp lệ hoặc bị mất kết nối"],
                "as_of_ms": now_ms,
            }

        # 4. Direction & Trend Alignment
        if htf_bias in ("BULLISH", "BEARISH"):
            direction = "LONG" if htf_bias == "BULLISH" else "SHORT"
        elif h1_alignment in ("BULLISH", "BEARISH"):
            direction = "LONG" if h1_alignment == "BULLISH" else "SHORT"
        else:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": "BIAS_UNKNOWN",
                "missing_conditions": ["Xu hướng khung lớn chưa xác định để tạo lệnh nghiên cứu"],
                "as_of_ms": now_ms,
            }

        bar_open_5m = int(getattr(closed_5m[-1], "open_time", None) or getattr(closed_5m[-1], "timestamp", 0))
        candidate_key = f"{model_id}_XAUUSDT_{direction}_{bar_open_5m}"

        import smc_engine
        atr_5m = smc_engine.compute_atr(closed_5m, period=14)
        if atr_5m <= 0:
            atr_5m = 2.0

        current_p = (bid + ask) / 2.0
        exec_price = ask if direction == "LONG" else bid

        # Deterministic setup geometry
        if direction == "LONG":
            sl_price = round(current_p - max(2.0 * atr_5m, 6.0), 2)
            entry_price = round(exec_price, 2)
            risk_dist = max(entry_price - sl_price, 1.0)
            target_price = round(entry_price + max(3.5 * risk_dist, 4.0 * atr_5m), 2)
        else:
            sl_price = round(current_p + max(2.0 * atr_5m, 6.0), 2)
            entry_price = round(exec_price, 2)
            risk_dist = max(sl_price - entry_price, 1.0)
            target_price = round(entry_price - max(3.5 * risk_dist, 4.0 * atr_5m), 2)

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": "INVALID_GEOMETRY",
                "missing_conditions": [f"Sai hình học giá: {geom_err}"],
                "as_of_ms": now_ms,
            }

        # Calculator check with fallback risk cap (PHẦN 055)
        capital = float(policy_settings.get("capital_usdt", 1000.0))
        eff_risk_pct = min(float(policy_settings.get("risk_pct", cfg.fallback_risk_pct)), cfg.fallback_risk_pct)
        lev = int(policy_settings.get("leverage", 50))
        margin_mode = str(policy_settings.get("margin_mode", "ISOLATED"))

        calc_res = calculate_risk_reward(
            direction=direction,
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            capital_usdt=capital,
            risk_pct=eff_risk_pct,
            leverage=lev,
            margin_mode=margin_mode,
            min_net_rr=cfg.min_net_rr,
        )

        if not calc_res.is_valid:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": "CALCULATOR_INVALID",
                "missing_conditions": [f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                "as_of_ms": now_ms,
            }

        if not calc_res.can_execute:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": "CANNOT_EXECUTE",
                "missing_conditions": [f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                "as_of_ms": now_ms,
            }

        if not calc_res.meets_min_rr:
            return {
                "is_eligible": False,
                "model_id": model_id,
                "candidate_key": candidate_key,
                "reason_code": OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                "missing_conditions": [f"Net R:R không đạt ngưỡng ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                "as_of_ms": now_ms,
            }

        ev_nodes = [
            EvidenceNode(
                id=f"ev_research_{now_ms}",
                kind="RESEARCH_POI", symbol="XAUUSDT", timeframe="5M", direction=direction,
                occurred_at_ms=now_ms, known_at_ms=now_ms, level=entry_price,
                extra={"htf_bias": htf_bias, "h1_alignment": h1_alignment, "research_tag": "NY_DAILY_RESEARCH"}
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
        }

        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="5M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.OBSERVING,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"Nghiên cứu NY Daily Research ({direction}) — Thử nghiệm thực thi giấy",
            config_version=cfg.config_version,
            detector_version="v15.0",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
            },
            evidence=ev_nodes,
        )

        return {
            "is_eligible": True,
            "model_id": model_id,
            "direction": direction,
            "candidate_key": candidate_key,
            "symbol": "XAUUSDT",
            "planned_entry": entry_price,
            "stop_loss": sl_price,
            "take_profit": target_price,
            "gross_rr": calc_res.gross_rr,
            "net_rr": calc_res.net_rr,
            "calculation": calc_res,
            "evidence": ev_nodes,
            "trigger_chain": trigger_chain,
            "snapshot": snap,
            "as_of_ms": now_ms,
        }
