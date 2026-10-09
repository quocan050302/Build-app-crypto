import uuid
import time
from typing import List
import schemas
from lab.replay_engine import ReplayEngine

class StressTester:
    """
    Parametric Stress Testing Engine for Aurum Desk V5.
    Evaluates strategy resilience under adverse market conditions:
    - Multiple spread multipliers (1x, 2x, 3x)
    - Directional slippage degradation (1x, 2x, 3x)
    - Adverse fee regimes (1x, 2x)
    - Simulated execution latency / delay
    Strictly isolated: runs entirely in simulation memory, producing structured sensitivity metrics.
    """

    @classmethod
    def run_stress_test(cls, request: schemas.StressTestRequest) -> schemas.StressTestResponse:
        start_time = int(time.time() * 1000)
        run_id = f"stress-{uuid.uuid4().hex[:8]}"

        # Baseline run
        base_req = schemas.ReplayRunRequest(
            run_name="Baseline",
            symbol=request.symbol,
            spread_multiplier=1.0,
            slippage_multiplier=1.0,
            fee_rate=0.0004,
            latency_ms=0,
            initial_equity=1000.0,
            risk_pct=0.25,
            leverage=30,
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

        # Iterate over parameter grids sequentially without GIL thread starvation
        for sm in request.spread_multipliers:
            for slm in request.slippage_multipliers:
                for fm in request.fee_multipliers:
                    for lat in request.latency_ms_list:
                        stress_req = schemas.ReplayRunRequest(
                            run_name=f"Stress S{sm} SL{slm} F{fm} L{lat}",
                            symbol=request.symbol,
                            spread_multiplier=sm,
                            slippage_multiplier=slm,
                            fee_rate=0.0004 * fm,
                            latency_ms=lat,
                            initial_equity=1000.0,
                            risk_pct=0.25,
                            leverage=30,
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
