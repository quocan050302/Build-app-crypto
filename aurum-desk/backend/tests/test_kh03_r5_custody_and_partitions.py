"""
aurum-desk/backend/tests/test_kh03_r5_custody_and_partitions.py

Unit & adversarial tests for KH-03.R5 DEV/QA custody journal, partition isolation,
crash-recovery quarantine, double-writer race, path traversal/symlink denial, tampered
raw bytes detection, DEV-as-OOS relabeling blocker, and deny-before-open OOS guard
(R5-F05, F22..F29, F47). NEVER touches historical OOS escrow!
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import sys
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r5_capture_and_custody_service import (
    OosAccessGuardR5,
    R5DevCustodyJournal,
    R5InjectedCrashError,
    R5OosAccessDeniedError,
    assign_r5_partition_before_capture,
    persist_r5_verified_dev_snapshot,
    verify_r5_custody_chain_readonly,
)


def _write_stage(tmp_path: Path, name: str, payload: bytes) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    sp = tmp_path / name
    sp.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    rec = {"raw_sha256": sha, "evidence_class": "QA_FIXTURE", "capture_proof_id": f"qa_proof_{sha[:8]}"}
    val = {"valid": True, "canonical_normalized_sha256": sha}
    return sp, rec, val


def test_r5_partition_blocks_oos_relabel_overlap_and_traversal_f24_f27_f47(tmp_path: Path) -> None:
    base = tmp_path / "allowed"
    base.mkdir()
    dest = base / "dev_run_1"
    dest.mkdir()
    plan_dev = {"partition_role": "DEVELOPMENT_CAPTURE", "plan_digest_sha256": "a" * 64}
    ledger = {"existing_intervals": [{"role": "REUSED_DEV", "symbol": "XAUUSDT", "start_ms": 1791437880000, "end_ms": 1793196000000}]}

    # GREEN: non-overlapping DEV partition
    ok = assign_r5_partition_before_capture(plan_dev, ledger, {"symbol": "XAUUSDT", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000}, destination_root=dest, allowed_base_root=base)
    assert ok["valid"] is True and ok["partition_role"] == "DEVELOPMENT_CAPTURE" and ok["permanent_dev_never_promotable_to_oos"] is True

    # RED F27: OOS role or known historical DEV SHA relabeled blocked!
    bad_oos = assign_r5_partition_before_capture({"partition_role": "FRESH_OOS"}, ledger, {"symbol": "XAUUSDT", "purpose": "OOS", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000}, destination_root=dest, allowed_base_root=base)
    assert bad_oos["valid"] is False
    bad_sha = assign_r5_partition_before_capture(plan_dev, ledger, {"symbol": "XAUUSDT", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000, "proposed_content_sha256": CANONICAL_DATASET_SHA256_KH02}, destination_root=dest, allowed_base_root=base)
    assert bad_sha["valid"] is False and any("HISTORICAL_SHA_CONTAMINATION" in v for v in bad_sha["violations"])

    # RED F47: Temporal overlap with existing DEV interval quarantined!
    bad_ov = assign_r5_partition_before_capture(plan_dev, ledger, {"symbol": "XAUUSDT", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1792000000000, "window_end_ms": 1794000000000}, destination_root=dest, allowed_base_root=base)
    assert bad_ov["valid"] is False and any("TEMPORAL_INTERVAL_OVERLAP" in v for v in bad_ov["violations"])

    # RED F24: Path traversal & symlink denied!
    bad_trav = assign_r5_partition_before_capture(plan_dev, ledger, {"symbol": "XAUUSDT", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000}, destination_root=base / ".." / "escape", allowed_base_root=base)
    assert bad_trav["valid"] is False
    sym_p = base / "sym_dest"
    os.symlink(tmp_path, sym_p)
    bad_sym = assign_r5_partition_before_capture(plan_dev, ledger, {"symbol": "XAUUSDT", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000}, destination_root=sym_p, allowed_base_root=base)
    assert bad_sym["valid"] is False


def test_r5_custody_crash_quarantine_double_writer_tamper_and_proof_replay_f05_f22_f23_f25_f26(tmp_path: Path) -> None:
    croot = tmp_path / "custody_dev"
    j = R5DevCustodyJournal(croot, os_disk_probe_fn=lambda: 28.0)
    part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "c" * 64}

    sp1, rec1, val1 = _write_stage(tmp_path, "s1.raw", b'{"code":"00000","data":[1]}')
    c1 = persist_r5_verified_dev_snapshot(sp1, rec1, val1, part, j)
    assert c1["committed"] is True and c1["seq_index"] == 0
    head_before = j.head_state()

    # F23: Concurrent double-writer of same snapshot -> idempotent (still 1 record, zero corruption!)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(persist_r5_verified_dev_snapshot, sp1, rec1, val1, part, j) for _ in range(2)]
        res_pair = [f.result() for f in futs]
    assert all(r["committed"] for r in res_pair)
    assert j.head_state()["count"] == 1

    # F05: Replaying consumed capture_proof_id on different bytes rejected!
    sp_rep, rec_rep, val_rep = _write_stage(tmp_path, "s_rep.raw", b'{"code":"00000","data":[999]}')
    rec_rep["capture_proof_id"] = rec1["capture_proof_id"]
    r_rep = persist_r5_verified_dev_snapshot(sp_rep, rec_rep, val_rep, part, j)
    assert r_rep["committed"] is False and any("CAPTURE_PROOF_ALREADY_CONSUMED" in v for v in r_rep["violations"])

    # F22: Crash between blob write/fsync and journal append -> previous head intact & partial quarantined!
    sp2, rec2, val2 = _write_stage(tmp_path, "s2.raw", b'{"code":"00000","data":[2]}')
    r_cr = persist_r5_verified_dev_snapshot(
        sp2, rec2, val2, part, j,
        _crash_hook=lambda ph: (_ for _ in ()).throw(R5InjectedCrashError(ph)) if ph == "after_blob_fsync" else None,
    )
    assert r_cr["committed"] is False and r_cr["quarantined"] is True and r_cr["previous_head_preserved"] is True
    assert j.head_state() == head_before
    assert verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())["valid"] is True

    # F25: Tampered raw bytes detected by independent readonly chain verifier!
    blob_p = croot / c1["blob_rel_path"]
    orig_b = blob_p.read_bytes()
    blob_p.write_bytes(orig_b + b" ")
    v_tam = verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())
    assert v_tam["valid"] is False and any("MODIFIED_RAW_BLOB_BYTES" in v for v in v_tam["violations"])

    # F26: Missing raw artifact detected!
    blob_p.unlink()
    v_mis = verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())
    assert v_mis["valid"] is False and any("MISSING_RAW_BLOB" in v for v in v_mis["violations"])


def test_r5_oos_deny_before_open_guard_f28_f29(tmp_path: Path) -> None:
    dev_dir = tmp_path / "dev_data"
    oos_dir = tmp_path / "real_oos_escrow"
    dev_dir.mkdir(); oos_dir.mkdir()
    guard = OosAccessGuardR5(allowed_dev_roots=[dev_dir], protected_oos_roots=[oos_dir])

    # Allowed DEV read
    f_dev = dev_dir / "bars.json"
    f_dev.write_text("{}")
    assert guard.assert_open_permitted(f_dev, caller="r5_diag", purpose="dev_readonly") == f_dev.resolve()

    # RED F28 & F29: OOS path & strategy_eval purpose denied BEFORE open!
    with pytest.raises(R5OosAccessDeniedError):
        guard.assert_open_permitted(oos_dir / "sealed.blob", caller="r5_diag", purpose="dev_readonly")
    with pytest.raises(R5OosAccessDeniedError):
        guard.assert_open_permitted(f_dev, caller="r5_diag", purpose="strategy_eval_new_dev")
    with pytest.raises(R5OosAccessDeniedError):
        R5DevCustodyJournal(tmp_path / "escrow_oos_isolated")

    s = guard.summary()
    assert s["oos_evaluations"] == 0 and s["oos_reads_executed"] == 0 and s["oos_denied_before_open_count"] == 2
