import uuid
import time
from typing import List, Dict, Any, Optional
import schemas
from lab.replay_engine import ReplayEngine

class StressTester:
    """
    Parametric Stress Testing Engine for Aurum Desk V12_2.
    Evaluates strategy resilience under adverse market conditions:
    - Multiple spread multipliers (1x, 2x, 3x)
    - Directional slippage degradation (1x, 2x, 3x)
    - Adverse fee regimes (1x, 2x)
    - Simulated execution latency / delay
    
    Supports:
    a) Full replay stress: runs ReplayEngine with identical dataset/config/variant,
       overriding declared costs and latency.
    b) Fixed trade-book cost repricing: clearly labeled FIXED_BOOK_COST_REPRICING,
       counting only delta slippage (not double-deducting base entry slip).
    """

    @classmethod
    def run_stress_test(cls, request: schemas.StressTestRequest) -> schemas.StressTestResponse:
        start_time = int(time.time() * 1000)
        run_id = f"stress-{uuid.uuid4().hex[:8]}"

        # Baseline run using request's actual parameters (D03 / D11 fix: no hardcoded 0.0004 fee)
        base_req = schemas.ReplayRunRequest(
            run_name=f"Baseline-{request.run_name}",
            symbol=request.symbol,
            strategy_variant=request.strategy_variant or "CURRENT_BASELINE",
            start_ts=request.start_ts,
            end_ts=request.end_ts,
            custom_dataset_dir=request.custom_dataset_dir,
            spread_multiplier=1.0,
            slippage_multiplier=1.0,
            spread_usd=request.base_spread_usd,
            slippage_usd=request.base_slippage_usd,
            fee_rate=request.base_fee_rate,
            maker_fee_rate=request.base_maker_fee_rate,
            latency_ms=0,
            initial_equity=request.initial_equity,
            risk_pct=request.risk_pct,
            quality_risk_pct=request.quality_risk_pct,
            quota_risk_pct=request.quota_risk_pct,
            leverage=request.leverage,
            warmup_days=request.warmup_days,
            mode=getattr(request, "mode", "HISTORICAL_MARKET"),
            export_artifacts=False
        )
        base_res = ReplayEngine.run_replay(base_req)
        baseline_row = schemas.StressTestResultRow(
            spread_mult=1.0,
            slippage_mult=1.0,
            fee_mult=1.0,
            latency_ms=0,
            trades_count=base_res.total_trades,
            net_pnl=base_res.total_net_pnl,
            win_rate_pct=base_res.win_rate_pct,
            profit_factor=base_res.profit_factor,
            max_drawdown_pct=base_res.max_drawdown_pct,
            expectancy_r=base_res.expectancy_r
        )

        matrix: List[schemas.StressTestResultRow] = []

        # Iterate over parameter grids sequentially without thread starvation
        for sm in request.spread_multipliers:
            for slm in request.slippage_multipliers:
                for fm in request.fee_multipliers:
                    for lat in request.latency_ms_list:
                        stress_req = schemas.ReplayRunRequest(
                            run_name=f"Stress S{sm} SL{slm} F{fm} L{lat}",
                            symbol=request.symbol,
                            strategy_variant=request.strategy_variant or "CURRENT_BASELINE",
                            start_ts=request.start_ts,
                            end_ts=request.end_ts,
                            custom_dataset_dir=request.custom_dataset_dir,
                            spread_multiplier=sm,
                            slippage_multiplier=slm,
                            spread_usd=round(request.base_spread_usd * sm, 4),
                            slippage_usd=round(request.base_slippage_usd * slm, 4),
                            fee_rate=round(request.base_fee_rate * fm, 6),
                            maker_fee_rate=round(request.base_maker_fee_rate * fm, 6),
                            latency_ms=lat,
                            initial_equity=request.initial_equity,
                            risk_pct=request.risk_pct,
                            quality_risk_pct=request.quality_risk_pct,
                            quota_risk_pct=request.quota_risk_pct,
                            leverage=request.leverage,
                            warmup_days=request.warmup_days,
                            mode=getattr(request, "mode", "HISTORICAL_MARKET"),
                            export_artifacts=False
                        )
                        res = ReplayEngine.run_replay(stress_req)
                        matrix.append(schemas.StressTestResultRow(
                            spread_mult=sm,
                            slippage_mult=slm,
                            fee_mult=fm,
                            latency_ms=lat,
                            trades_count=res.total_trades,
                            net_pnl=res.total_net_pnl,
                            win_rate_pct=res.win_rate_pct,
                            profit_factor=res.profit_factor,
                            max_drawdown_pct=res.max_drawdown_pct,
                            expectancy_r=res.expectancy_r
                        ))

        return schemas.StressTestResponse(
            id=run_id,
            run_name=request.run_name,
            symbol=request.symbol,
            baseline=baseline_row,
            stress_matrix=matrix,
            created_at=start_time
        )

    @staticmethod
    def reprice_closed_trade_book(
        trades: List[schemas.ReplayTradeItem],
        base_fee_rate: float,
        base_maker_rate: float,
        base_slippage_usd: float,
        stressed_fee_rate: float,
        stressed_maker_rate: float,
        stressed_slippage_usd: float,
        stressed_spread_usd: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        D11/E08: Fixed trade-book cost repricing.
        Clearly labeled FIXED_BOOK_COST_REPRICING.
        Does NOT double deduct base entry slippage: only subtracts delta slippage.
        """
        total_pnl = 0.0
        delta_slip_per_unit = max(0.0, stressed_slippage_usd - base_slippage_usd)
        
        for t in trades:
            if t.status != "CLOSED":
                continue
            gross = t.gross_pnl
            # Recalculate fees at stressed rates
            ent_fee = t.entry_price * t.quantity * stressed_fee_rate
            ex_rate = stressed_maker_rate if (t.exit_cause == "TP_HIT" and getattr(t, "tp_is_maker", False)) else stressed_fee_rate
            ex_fee = t.exit_price * t.quantity * ex_rate
            
            # Delta slippage applied only for excess above base
            ent_delta_slip = t.quantity * delta_slip_per_unit
            ex_delta_slip = t.quantity * delta_slip_per_unit if t.exit_cause != "TP_HIT" else 0.0
            
            # Additional spread impact if spread was stressed and simulated
            spread_delta = 0.0
            if stressed_spread_usd and stressed_spread_usd > 0.35:
                spread_delta = t.quantity * (stressed_spread_usd - 0.35) * 0.5
            
            trade_net = gross - (ent_fee + ex_fee) - (ent_delta_slip + ex_delta_slip) - spread_delta
            total_pnl += trade_net
            
        return {
            "model_type": "FIXED_BOOK_COST_REPRICING",
            "trades_count": len([t for t in trades if t.status == "CLOSED"]),
            "stressed_net_pnl": round(total_pnl, 2),
            "delta_slippage_used": delta_slip_per_unit
        }
