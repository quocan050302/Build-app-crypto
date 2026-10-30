# AURUM DESK V17.8.0.3 — SHADOW DATABASE ISOLATION SPECIFICATION

**Release:** `V17.8.0.3`  
**Module:** `backend/services/shadow_research_storage.py`

---

## 1. Dedicated Shadow Research SQLite Storage

`MARKET_SHADOW` persists research observations into a dedicated SQLite database managed by `ShadowResearchStorage`:
- Default runtime path: `<repo_root>/shadow_data/aurum_shadow_research.db` (overridable via `AURUM_SHADOW_DB_PATH`).
- Test-isolated path: `<test_root>/shadow_research_<run_id>.db`.
- Engine marker: `_aurum_shadow_research_engine = True` so the operational database write guard in `backend/database.py` never confuses shadow research writes with operational DB mutations, while still blocking any write to the operational SQLite file.

---

## 2. Path & Ownership Safety Guards

`resolve_and_validate_shadow_db_path()` enforces 6 fail-closed checks before opening or creating the shadow database:
1. **Symlink Rejection (`SHADOW_DB_SYMLINK_REJECTED`)**: Rejects any path where the target file or any ancestor directory is a symbolic link.
2. **Directory Rejection (`SHADOW_DB_PATH_INVALID`)**: Rejects paths resolving to a directory.
3. **Operational DB Conflict Rejection (`SHADOW_DB_OPERATIONAL_CONFLICT`)**: Rejects any path matching `database.RUNTIME_DEFAULT_DB_PATH`, `database.DB_PATH`, or containing `aurum_desk.db` in its filename.
4. **Forbidden Root Rejection (`SHADOW_DB_FORBIDDEN_ROOT`)**: Rejects placing the shadow DB directly inside repository root or `backend/` root without a dedicated subdirectory.
5. **Hardlink & Inode Collision Rejection (`SHADOW_DB_HARDLINK_REJECTED`)**: Rejects files with `st_nlink > 1` or sharing `(st_dev, st_ino)` with the operational database.
6. **Table Contamination Rejection (`SHADOW_DB_OWNERSHIP_VIOLATION`)**: Inspects existing SQLite tables in read-only mode and rejects any database containing operational tables (`paper_orders`, `day_audits`, `trading_policies`, `watch_setups`).

---

## 3. Isolated Shadow Schema

`ShadowBase` defines 9 research-only tables (and zero operational order tables):
1. `shadow_storage_ownership`
2. `shadow_market_quotes`
3. `shadow_candles`
4. `shadow_analysis_cycles`
5. `shadow_unicorn_evidence`
6. `shadow_stop_decisions`
7. `shadow_target_decisions`
8. `shadow_opportunities`
9. `shadow_replay_results`
10. `shadow_runtime_health`

