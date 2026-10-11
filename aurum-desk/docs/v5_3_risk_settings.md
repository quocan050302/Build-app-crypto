# V5.3 Risk Settings & Metadata Architecture

This document describes the architectural improvements implemented in V5.3 of Aurum Desk regarding Risk Settings and dynamic instrument metadata.

## 1. Context and Goals
Prior to V5.3, `leverage`, `margin_mode`, and `risk_pct` (position sizing risk) were loosely coupled and inconsistently evaluated between the frontend polling loops, backend strategy engine, and order executor. 
Changes via the frontend occasionally caused race conditions where polling overwrote user drafts.
In addition, margin maintenance tiers and fee schedules were hardcoded rather than dynamically fetched.

**Goals for V5.3:**
1. Centralized, versioned Risk Settings stored persistently.
2. Dynamic fetching of instrument metadata (tiers, leverage limits, fees) via `InstrumentProvider`.
3. Strict re-validation of `ARMED` orders if Risk Settings are mutated before they execute.
4. Dirty flags in the frontend to avoid polling loops wiping out unsaved draft settings.

## 2. Architecture Changes

### A. The `InstrumentProvider`
We created a new backend service `InstrumentProvider` located in `backend/services/instrument_provider.py`.
- Responsible for querying Bitget's API for actual tiered leverage and maintenance margin rates.
- Caches results locally with a defined TTL.
- Falls back to conservative `fallback_tiers` if Bitget API is unreachable.
- `domain_calculator.py` has been updated to query this provider synchronously during position size calculations.

### B. Persistent Versioned Risk Settings
- Schema `RiskSettingsUpdate` and `RiskSettingsResponse` added in `schemas.py`.
- `models.SystemConfig` now stores `default_leverage`, `default_margin_mode`, `default_risk_pct`, and a critical `risk_config_version`.
- Updates to settings bump the `risk_config_version` via Optimistic Concurrency Control (CAS).

### C. Execution Re-validation
- `ExecutionCoordinator` (`evaluate_orders_sync`) now checks the `config_version` of an `ARMED` order against the global `SystemConfig` `risk_config_version`.
- If stale, the order's geometry and size are automatically recalculated via `domain_calculator.py` using the new risk variables.
- If the new settings render the trade unsafe (e.g., liquidation before SL or margin too high), it is automatically rejected with `RISK_SETTINGS_INVALIDATED`.

### D. Frontend Drafts
- `App.tsx` now tracks `isSettingsDirty`.
- The regular polling from `refreshAccountAndHealth` only overwrites the local form state if `isSettingsDirty` is false.
- The settings endpoint includes the `expected_config_version` to handle parallel edit conflicts.

## 3. Financial Semantics & Rules
- **Quantity Sizing**: `quantity = (Equity * RiskPct / 100) / Risk_Per_Unit`. Changing leverage *does not* multiply quantity.
- **Liquidation Guard**: Liquidation price MUST remain behind the Stop Loss to prevent catastrophic failure before standard exit.
- **Cross Margin**: Modifiable in the UI for theoretical preview, but execution is blocked with `CROSS_MARGIN_UNSUPPORTED` in the Paper Broker to protect the invariant of strict Isolated Risk per setup.

## 4. Migration & Schema Changes
- Added `risk_pct` and `config_version` to `models.WatchSetup`.
- Added `config_version` to `models.PaperOrder`.
- `main.py`'s `lifespan` hook automatically upgrades older database schemas via an `ALTER TABLE` execution block on startup.
