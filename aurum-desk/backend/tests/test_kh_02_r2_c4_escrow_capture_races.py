"""
aurum-desk/backend/tests/test_kh_02_r2_c4_escrow_capture_races.py

AURUM DESK — KH-02.R2.C4 Concurrent Collector/Escrow Races, Crash/Quarantine & Idempotency Tests:
  - 2/4/8 threads racing on the same capture receipt + escrow -> exact-once append + idempotent noops.
  - 3 separate OS processes contending via cross-platform OS lock (global descendants <= 4).
  - Crash between staged blob and journal append -> fail-closed until explicit quarantine, 0 real OOS pollution.
  - Modifying raw bytes after capture -> rejected inside escrow lock even when reusing a valid receipt.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal, make_crash_hook_for_phase
from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    _SequenceFakeTransportC2,
)
from services.kh_02_r2_prospective_data_service import R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS


def _make_qa_capture(sid: str, offset_ms: int = 300_000) -> dict:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + offset_ms
    body = json.dumps(
        {"code": "00000", "msg": "success", "data": [{"symbol": "XAUUSDT", "bidPr": "4020.10", "askPr": "4020.30", "ts": str(t0)}]}
    ).encode("utf-8")
    resp = RawHttpResponseC2(200, body, {"content-type": "application/json"}, t0 + 5, t0 + 20)
    auth = OperatorCaptureAuthorizationC2(approval_id="C4_RACE_QA", approved_by="qa", network_capture_enabled=True)
    col = C2PublicReadOnlyBitgetCollector(authorization=auth, transport=_SequenceFakeTransportC2([resp]), is_fake_transport_qa=True)
    return col.fetch_public_l1_bbo_snapshot(snapshot_id=sid, write_mount_probe={"can_run_heavy_stages": True, "min_free_gib": 35.0})


def test_c4_concurrent_capture_escrow_idempotency_and_modified_bytes_race(tmp_path: Path) -> None:
    cap = _make_qa_capture("c4_race_same")
    qa_j = C2DurableEscrowJournal(tmp_path / "qa_race", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    for nw in (2, 4, 8):
        j_n = C2DurableEscrowJournal(tmp_path / f"qa_race_{nw}", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
        bar = threading.Barrier(nw)

        def _w(_i: int) -> str:
            bar.wait()
            return j_n.append_verified_prospective_snapshot(
                cap["snapshot_contract_entry"],
                raw_payload_bytes=cap["raw_response_bytes"],
                capture_receipt=cap["capture_receipt"],
            )["action"]

        with ThreadPoolExecutor(max_workers=nw) as pool:
            acts = list(pool.map(_w, range(nw)))
        assert acts.count("APPENDED_NEW_ENTRY") == 1
        assert acts.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == nw - 1
        assert j_n.verify_integrity()["valid"] is True

    # Modifying raw bytes after capture fails closed
    tampered_bytes = cap["raw_response_bytes"].replace(b"4020.10", b"4020.20")
    with pytest.raises((ValueError, PermissionError)):
        qa_j.append_verified_prospective_snapshot(
            cap["snapshot_contract_entry"],
            raw_payload_bytes=tampered_bytes,
            capture_receipt=cap["capture_receipt"],
        )


def test_c4_crash_between_blob_and_journal_and_3_os_proc_contention(tmp_path: Path) -> None:
    cap_crash = _make_qa_capture("c4_crash_sid", offset_ms=360_000)
    qa_j = C2DurableEscrowJournal(tmp_path / "qa_crash", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    with pytest.raises(RuntimeError, match="AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND"):
        qa_j.append_verified_prospective_snapshot(
            cap_crash["snapshot_contract_entry"],
            raw_payload_bytes=cap_crash["raw_response_bytes"],
            capture_receipt=cap_crash["capture_receipt"],
            _crash_hook=make_crash_hook_for_phase("AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND"),
        )
    assert qa_j.verify_integrity()["valid"] is False
    q = qa_j.quarantine_corrupt_tail_and_orphans(operator_reason="C4_CRASH_TEST_RECOVERY")
    assert q["quarantined_count"] == 1
    assert q["post_recovery_integrity"]["valid"] is True

    # 3 separate OS processes contending cleanly via cross-platform OS lock (1 pytest + 3 children <= 4)
    mp_dir = tmp_path / "mp_c4_os_lock"
    code = "\n".join([
        "import hashlib, json, sys; from pathlib import Path",
        "from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal",
        "from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture",
        "j = C2DurableEscrowJournal(Path(sys.argv[1])); w = int(sys.argv[2]); out = []",
        "s1 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f'c4_p{w}')",
        "s1['dataset_sha256'] = hashlib.sha256(f'c4_p{w}'.encode()).hexdigest()",
        "out.append(j.append_snapshot(s1)['action'])",
        "sh = make_synthetic_valid_c1_snapshot_fixture(snapshot_id='c4_shared')",
        "sh['dataset_sha256'] = 'e' * 64",
        "out.append(j.append_snapshot(sh)['action'])",
        "print(json.dumps(out))",
    ])
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(mp_dir), str(i)], cwd=str(APP_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(3)
    ]
    priv_a, shared_a = [], []
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, err
        arr = json.loads(out.strip())
        priv_a.append(arr[0])
        shared_a.append(arr[1])
    assert priv_a.count("APPENDED_NEW_ENTRY") == 3
    assert shared_a.count("APPENDED_NEW_ENTRY") == 1
    assert shared_a.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 2
    chk = C2DurableEscrowJournal(mp_dir).verify_integrity()
    assert chk["valid"] is True and chk["record_count"] == 4
