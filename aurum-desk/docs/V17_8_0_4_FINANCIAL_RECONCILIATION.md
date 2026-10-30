# AURUM DESK V17.8.0.4 — TRADE-LEVEL & AGGREGATE FINANCIAL RECONCILIATION

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Backtest Run ID:** `v17_8_0_4_bt_1793248253`
- **Verification Gate:** `G84_TRADE_LEVEL_PNL_RECONCILIATION` (`PASS`)

## 2. Exact Accounting Reconciliation (`gross_pnl - fees_paid - slippage_cost == net_pnl`)
Every trade in `backtest/trade_level_results.json` and every variant summary in `baseline_A.json`, `structural_B.json`, `unicorn_C.json`, and `noise_D.json` is verified by `reconcile_variant_financial_metrics()` to within `1e-2` USDT:

| Variant | Simulated Trades | Gross PnL (USDT) | Fees Paid (USDT) | Slippage Cost (USDT) | Net PnL (USDT) | Net Expectancy (R) |
|---|---:|---:|---:|---:|---:|---:|
| **A (`LEGACY_FIXED`)** | `30` | `-3.5563` | `47.6788` | `1.459` | `-52.6941` | `-0.7276` |
| **B (`STRUCTURAL_FLOOR`)** | `17` | `-3.7671` | `17.1566` | `0.527` | `-21.4507` | `-0.5235` |
| **C (`UNICORN_DYNAMIC`)** | `17` | `-3.1595` | `17.6706` | `0.543` | `-21.3731` | `-0.5235` |
| **D (`EMPIRICAL_NOISE`)** | `17` | `-5.4142` | `15.9212` | `0.489` | `-21.8244` | `-0.5322` |
