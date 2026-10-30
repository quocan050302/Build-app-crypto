# V17.9.0.1.2 — Backtest Source SHA Provenance & Amendment Forensics Report (`DEF-179012-F03`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `21cc6876cd9e2a846adf96d22a4d2061b8bf4e98`
- **Backtest Executed Source SHA:** `2a494323d87f0417fbc2b118f26c788a07140672`
- **Executable Source Tree SHA-256:** `0db5661675b4f684f29fdc437bcfd356dcfdbdb3c6c05834877142b15415b389` (`113` files)
- **Backtest Source SHA Provenance Status:** `FAIL` (`violations=['BACKTEST_SOURCE_SHA_MISMATCH_WITH_FINAL_CODE_SHA']`)
- **Trade Ledger Cache Validation Status:** `CACHE_VALID` (`cache_trusted=True`)

## 1. Forensics of Previously Reported SHA `3b40be13fe2fa84af99d541acc88bcace17985e2`
- **Classification:** `AMENDED_AWAY_PRE_AMEND_COMMIT`.
- **Root Cause:** During V17.9.0.1.2 closure, executable code was committed at `3b40be13fe2fa84af99d541acc88bcace17985e2` (`2026-10-30 01:02:27 +0700`), `v17_8_backtest_runner.py` executed against that commit, and subsequently `git commit --amend` rewrote the commit into `0aa80ed0921662650807a42ec98292cfec83c874` (`2026-10-30 01:15:39 +0700`) without re-running `v17_8_backtest_runner.py`.
- **Prevention (`N61`–`N68`):** Added `verify_backtest_source_sha_provenance()`, `compute_executable_source_tree_hash()`, and `validate_cached_trade_ledger_provenance()` enforcing 11 cache-key invariants and rejecting any unresolvable, dangling, or mismatched `source_sha`.
