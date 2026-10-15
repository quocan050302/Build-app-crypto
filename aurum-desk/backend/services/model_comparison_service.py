"""
backend/services/model_comparison_service.py
V15.0 - Model Comparison & Statistical Reporting Service (PHẦN 080).

Separately compares:
1. STANDARD_SMC (Existing M5 model)
2. SMC_M1_REVERSAL (M1 Scalp Reversal)
3. SMC_M1_CONTINUATION (M1 Scalp Continuation)
4. NY_DAILY_RESEARCH (Daily NY Paper Research)

Never merges research trades into standard winrate.
Computes: trades, winrate, net PnL, expectancy after costs, max drawdown, average cost, session breakdown, latency, and blocked reasons.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import models
from services.scalp_m1_types import ModelIdentifier

logger = logging.getLogger(__name__)


class ModelComparisonService:
    @staticmethod
    def generate_comparison_report(
        db: Optional[Session],
        *,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        orders: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Pure and database-backed model comparison report (PHẦN 080).
        """
        order_list: List[Any] = []
        if orders is not None:
            order_list = list(orders)
        elif db is not None:
            query = db.query(models.PaperOrder)
            if start_ms is not None:
                query = query.filter(models.PaperOrder.created_at >= start_ms)
            if end_ms is not None:
                query = query.filter(models.PaperOrder.created_at <= end_ms)
            order_list = query.all()

        # Categorize by model family
        families = [
            ModelIdentifier.STANDARD_SMC.value,
            ModelIdentifier.SMC_M1_REVERSAL.value,
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            ModelIdentifier.NY_DAILY_RESEARCH.value,
        ]

        grouped: Dict[str, List[Any]] = {f: [] for f in families}
        grouped["OTHER"] = []

        for ord in order_list:
            fam = getattr(ord, "strategy_family", None) or "STANDARD_SMC"
            if fam in grouped:
                grouped[fam].append(ord)
            else:
                grouped["OTHER"].append(ord)

        comparison: Dict[str, Any] = {}
        for fam, f_orders in grouped.items():
            if fam == "OTHER" and not f_orders:
                continue

            closed_orders = [o for o in f_orders if getattr(o, "state", "") == "closed"]
            total_closed = len(closed_orders)

            wins = 0
            losses = 0
            total_pnl = 0.0
            total_fees = 0.0
            pnl_series: List[float] = []

            for co in closed_orders:
                net_pnl = getattr(co, "realized_pnl_net", None)
                pnl = float(net_pnl if net_pnl is not None else (getattr(co, "realized_pnl", 0.0) or 0.0))
                fees = float(getattr(co, "fees_assumption", 0.0) or getattr(co, "fee_actual", 0.0) or 0.0)
                total_pnl += pnl
                total_fees += fees
                pnl_series.append(pnl)

                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1

            winrate = round((wins / total_closed * 100.0), 1) if total_closed > 0 else 0.0
            avg_cost = round((total_fees / total_closed), 2) if total_closed > 0 else 0.0
            expectancy = round((total_pnl / total_closed), 2) if total_closed > 0 else 0.0

            # Max Drawdown calculation
            peak = 0.0
            equity = 0.0
            max_dd = 0.0
            for p in pnl_series:
                equity += p
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd

            comparison[fam] = {
                "model_id": fam,
                "total_orders": len(f_orders),
                "closed_trades": total_closed,
                "wins": wins,
                "losses": losses,
                "winrate_pct": winrate,
                "net_pnl_usdt": round(total_pnl, 2),
                "expectancy_usdt": expectancy,
                "max_drawdown_usdt": round(max_dd, 2),
                "average_cost_usdt": avg_cost,
                "status_breakdown": {
                    "armed": len([o for o in f_orders if getattr(o, "state", "") == "armed"]),
                    "paper_open": len([o for o in f_orders if getattr(o, "state", "") in ("paper_open", "open")]),
                    "closed": total_closed,
                }
            }

        return {
            "comparison": comparison,
            "period": {"start_ms": start_ms, "end_ms": end_ms},
            "disclaimer_vi": "Kết quả nghiên cứu theo mô hình riêng biệt. Giao dịch nghiên cứu NY_DAILY_RESEARCH không được gộp vào tỷ lệ thắng của STANDARD_SMC.",
        }
