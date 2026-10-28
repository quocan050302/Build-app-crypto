# AURUM DESK V17.8.0.3 — REAL HISTORICAL BACKTEST RESULTS

**Release:** `V17.8.0.3`  
**Dataset SHA-256:** `e2c972875e4517c88873c1f6f533db5844ca66f3c2254cd2e720cb028f3dcfa7`  
**Evaluated Setups:** `5,440` (`3,808` In-Sample / `1,632` Out-of-Sample)

---

## 1. Matched Comparison Across Variants A / B / C / D (Full Sample)

| Metric | Variant A (Legacy) | Variant B (V17.7 Structural) | Variant C (V17.8 Unicorn) | Variant D (Noise Research) |
|---|---:|---:|---:|---:|
| Opportunities Evaluated | `5,440` | `5,440` | `5,440` | `5,440` |
| Eligible Setups | `67` | `28` | `28` | `27` |
| Rejected Setups | `5,373` | `5,412` | `5,412` | `5,413` |
| Simulated Trades | `66` | `28` | `28` | `27` |
| Wins / Losses | `8 / 58` | `5 / 23` | `5 / 23` | `4 / 23` |
| Win Rate (`%`) | `12.12%` | `17.86%` | `17.86%` | `14.81%` |
| Gross PnL (USDT) | `-42.7327` | `-29.1045` | `-29.2775` | `-29.9781` |
| Total Fees (USDT) | `49.9648` | `8.9351` | `8.7582` | `8.4406` |
| **Net PnL (USDT)** | **`-92.6975`** | **`-38.0396`** | **`-38.0357`** | **`-38.4187`** |
| **Net Expectancy (`R`)** | **`-0.5851R`** | **`-0.5579R`** | **`-0.5579R`** | **`-0.5908R`** |
| Profit Factor | `0.4008` | `0.3821` | `0.3821` | `0.3282` |
| **Max Drawdown (USDT)** | **`92.6975`** | **`48.6681`** | **`48.6642`** | **`48.5180`** |
| Avg Stop Distance (px) | `5.8403` | `10.1729` | `10.3543` | `10.4222` |
| Avg Quantity | `0.4835` | `0.2682` | `0.2629` | `0.2626` |
| Max Loss Per Trade | `2.4633` | `2.4567` | `2.4557` | `2.4557` |
| Early Stop-Out `<= 30s` | `UNAVAILABLE_FROM_M1_OHLC` | `UNAVAILABLE_FROM_M1_OHLC` | `UNAVAILABLE_FROM_M1_OHLC` | `UNAVAILABLE_FROM_M1_OHLC` |
| **Early Stop-Out `<= 60s`** | **`14`** | **`2`** | **`2`** | **`2`** |
| **Early Stop-Out `<= 180s`** | **`29`** | **`2`** | **`2`** | **`2`** |
| **Post-Stop Recovery Rate** | **`3.64%`** | **`0.0%`** | **`0.0%`** | **`0.0%`** |

---

## 2. Key Quantitative Findings (Honest Engineering Interpretation)

1. **85.7% Reduction in Early Stop-Outs (`<= 60s`) & 93.1% Reduction (`<= 180s`)**:
   - Variant A (Legacy tight stop) suffered `14` stop-outs within 60 seconds and `29` stop-outs within 180 seconds, with a `3.64%` post-stop recovery rate to TP.
   - Variants B and C (V17.7 Structural and V17.8 Unicorn Structural + Dynamic Buffer) reduced `<= 60s` stop-outs from `14` to `2` (`-85.7%`), reduced `<= 180s` stop-outs from `29` to `2` (`-93.1%`), and eliminated post-stop recovery to TP (`0.0%`).
2. **59.0% Reduction in Net Loss & 47.5% Reduction in Max Drawdown**:
   - Variant C improved Net PnL from `-92.6975 USDT` (Variant A) to `-38.0357 USDT` and cut Max Drawdown from `92.6975 USDT` to `48.6642 USDT`, while strictly respecting the `$2.50` per-trade risk cap (`max_loss_per_trade = 2.4557 USDT`).
3. **Honest Expectancy Disclosure**:
   - Across this 15-day M1 historical window, all four variants exhibited negative net post-fee expectancy (`-0.5579R` for Variant C vs `-0.5851R` for Variant A). Accordingly, V17.8.0.3 keeps all trading authorization at **ZERO** (`MARKET_SHADOW` / `OBSERVE_ONLY` research only).

