# V17.9.0.1.3 R2 Defect Closure Matrix

- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Final Code SHA:** `1572393c130bec73a3e4a9d089d664a7705ab431`
- **Pre-Refactor Reference Baseline SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

| Defect ID | Severity | Root Cause | Actual Code Repair | Verification Tests & Negative Controls | Final Status |
|---|---|---|---|---|---|
| `P0-A` | P0 | Historical Production decision contained inferred/hardcoded values (`qty=0.34`, `VALID`, `STRUCTURAL_SWING`, `dec_{model_id}_{ts}`) and model substring matching. | Extracted authentic candidate attributes, linked `PaperOrder` quantity from `mem_db`, assigned `qty=None` when unconsumed, added `cmp_dec_` correlation prefix, eliminated substring matching. | `N96`, `N98`, `N99`, `N100`, `N101`, `N102` | `CLOSED_VERIFIED` |
| `P0-B` | P0 | Cross-runtime canonical hashing reused shared contexts; `INDEPENDENT_RUNTIME_INPUT_HASHES.json` published empty `{}`. | Replaced shared context reuse with independent runtime boundary input captures (`base_in`, `shared_in`, `prod_in`, `shadow_in`, `bt_in`) and populated full non-empty artifact. | `N103`, `N104`, `N105`, `N106`, `N107`, `N108`, `N109`, `N110` | `CLOSED_VERIFIED` |
| `P0-C` | P0 | Acceptance verifier had fail-open defaults (`hist_st='PASS'`, `neg_passed=95`, `disk_st='PASS'`, `free_bytes=50GB`, strategy verdict PASS without data). | Replaced all optimistic defaults with strict fail-closed checks in `derive_authoritative_acceptance_verdicts` and verified disk bytes directly. | `N111`, `N112`, `N113`, `N114`, `N115`, `N118`, `N119`, `N120`, `N121`, `N122` | `CLOSED_VERIFIED` |
| `P0-D` | P0 | Backtest source SHA differed from tested source SHA (`21cc...` vs `2a49...`). | Enforced exact frozen commit equality (`bt_source_sha == final_code_sha`), verified frozen executable source tree equivalence, and prohibited artifact relabeling. | `N116`, `N117`, `N131` | `CLOSED_VERIFIED` |
