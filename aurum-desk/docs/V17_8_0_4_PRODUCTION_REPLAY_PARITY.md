# AURUM DESK V17.8.0.4 — 5-MODEL PRODUCTION vs REPLAY vs SHADOW DECISION PARITY (`DEF-17803-07`)

## 1. Executive Summary
- **Defect Closed:** `DEF-17803-07` (Backtest Opportunity Extractor Diverges From Production Strategy)
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Backtest Run ID:** `v17_8_0_4_bt_1793248253`
- **Verification Gate:** `G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY` (`PASS`)

## 2. Production-Equivalent 5-Model Evaluation Architecture
Historical replay (`backend/services/v17_8_backtest_runner.py`) and `MARKET_SHADOW` (`backend/services/market_shadow_service.py`) evaluate all 5 production strategy models chronologically on closed candles with zero lookahead:
1. `SMC_M1_FVG_CONTINUATION`: Post-confirmation M1 FVG retest + HTF/M1 structure alignment + strict causal Unicorn check (`strict_causal_binding=True`).
2. `SMC_M1_CONTINUATION`: Confirmed displacement + pullback into causal OB/FVG discount/premium zone.
3. `SMC_M1_REVERSAL`: Liquidity sweep + CHOCH/MSS displacement reversal confirmation.
4. `SMC_M1_SWEEP_RECLAIM`: Liquidity pool sweep + close reclaim inside prior range + causal structural anchor.
5. `CG01`: Classic Gold 15M/5M/1M structural sweep & displacement setup.

## 3. Parity Verification Results
Across identical candle windows, quotes, and `StopPlacementPolicy` settings, Production, Replay, and `MARKET_SHADOW` produce identical setup classifications, `stop_anchor` prices, `take_profit` targets, and `calculate_risk_reward` eligibility decisions (`3,839` valid historical opportunities and `833` early-stage rejections extracted chronologically).
