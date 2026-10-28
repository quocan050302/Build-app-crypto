# AURUM DESK V17.8 — DETERMINISTIC HISTORICAL REPLAY & RESEARCH PROTOCOL

## 1. Overview & Matched-Comparison Rules
To scientifically measure whether V17.8 Unicorn-assisted structural stops resolve the early stop-out problem without degrading strategy expectancy, the replay harness (`backend/services/unicorn_replay_service.py`) evaluates 4 Stop Loss variants under strict matched-comparison conditions.

### Matched Comparison Constraints:
1. **Identical Candidate Entry:** Same entry price, same direction, same timestamp.
2. **Identical Take Profit:** Same independently resolved liquidity target.
3. **Identical Cost Assumptions:** Bitget fee model (0.02% maker, 0.04% taker) and $0.10 slippage.
4. **Identical Risk Budget:** Exactly $2.50 USDT (0.25% of 1000 USDT) @ 20x leverage.
5. **Conservative Intrabar Collision:** If both SL and TP are touched within the same 1-minute candle, the harness flags `ambiguous = True` and conservatively assigns outcome `"SL"`.

---

## 2. The Four Variants Under Evaluation

| Variant | Name | Description | Role |
|---|---|---|---|
| **A** | Legacy Edge Stop | FVG Edge + 5 ticks ($0.05 on XAUUSDT) | Baseline / Control |
| **B** | V17.7 Structural Stop | Protected origin swing + dynamic buffer | Intermediate Control |
| **C** | V17.8 Unicorn Stop | Breaker distal invalidation + dynamic buffer | Primary V17.8 Target |
| **D** | Researched Noise Stop | Breaker distal + P90 noise buffer (1.25x) | Research Only |

---

## 3. Monitored Metric Vector
- `total_candidates`, `eligible_candidates`, `rejected_candidates`, `executed_trades`
- `early_sl_30s`: Trades stopped out within 30 seconds of entry.
- `early_sl_60s`: Trades stopped out within 60 seconds of entry.
- `early_sl_180s`: Trades stopped out within 180 seconds of entry.
- `win_rate`: $\frac{Wins}{Executed}$
- `average_win_R`, `average_loss_R`
- `net_expectancy`: $(WinRate \times AvgWinR) - ((1 - WinRate) \times AvgLossR)$
- `max_drawdown`: Peak-to-trough cumulative R drawdown.
- `mae_avg`, `mfe_avg`: Maximum adverse / favorable excursion.
- `post_stop_recovery_rate`: Percentage of stopped-out trades where price later reached TP.
- `ambiguous_exits`, `unverifiable_exits`
