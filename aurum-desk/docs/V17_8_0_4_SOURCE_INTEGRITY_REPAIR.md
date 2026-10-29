# AURUM DESK V17.8.0.4 — SOURCE INTEGRITY & GIT MANIFEST REPAIR (`DEF-17803-06`)

## 1. Executive Summary
- **Release Version:** `V17.8.0.4`
- **Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Final Executable Code SHA (`FINAL_CODE_SHA`):** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Committed Git-Tree Source Composite SHA:** `4eba3fb586cda938a1232ef77728a186390165df042277831bbe6dc99e11f0ac`
- **Tracked Source Files Count:** `867`
- **Untracked / OS Noise Files Included:** `0` (`git_tracked_only = True`)
- **Acceptance Run ID:** `run_v17_8_0_4_1793248273004_25296`

## 2. Root Cause of `DEF-17803-06`
In V17.8.0.3 (`run_v17_8_0_3_1793203227963_81705`), `build_source_manifest()` walked the local filesystem using `rglob("*")` instead of enumerating the committed Git tree (`git ls-tree -r HEAD`). As a result:
1. Local untracked files (`backend/services/market_shadow_Types.py` and `backend/tests/test_v17_8_0_3_smoke.py`) and `.DS_Store` were hashed into `source_manifest.json` (`868` entries vs `865` committed files).
2. An independent auditor checking out the committed Git SHA could not reproduce `composite_sha`.

## 3. Architectural Repair Implemented in `tools/artifact_verifier.py`
1. **Git-Committed Tree Enumeration:** `build_source_manifest(root_dir, commit_sha=None, require_clean_worktree=False)` now enumerates files exclusively via `git ls-tree -r -z <commit_sha>` and reads blob contents directly from the Git object database via `git cat-file --batch` (supporting `.git` at either `root_dir` or `root_dir.parent`).
2. **Line-Ending Canonicalization:** Text blobs normalize `\r\n` to `\n` before SHA-256 hashing so cross-platform checkouts produce identical leaf and composite hashes.
3. **OS Noise Exclusion & Negative Controls (`G77`, `G78`):** `.DS_Store` and `Thumbs.db` are unconditionally excluded (`EXCLUDED_FILENAMES`), and `verify_source_manifest_against_git()` enforces all 7 mandatory negative controls (untracked file injection, `.DS_Store` injection, modified tracked file, missing tracked file, composite SHA tamper, dirty worktree rejection when `check_worktree_clean=True`, and valid clean committed tree acceptance).
