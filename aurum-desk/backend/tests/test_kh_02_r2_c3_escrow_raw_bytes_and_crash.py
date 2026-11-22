"""
aurum-desk/backend/tests/test_kh_02_r2_c3_escrow_raw_bytes_and_crash.py

AURUM DESK — KH-02.R2.C3 P0-C Durable Escrow Raw-Bytes, EINTR/ENOSPC/Short-Write,
             Crash-Before-Anchor (`UNANCHORED_PENDING_ATTESTATION`), Double Quarantine,
             and Multi-Process Concurrency Tests (Attacks 18-22).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
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

from services.kh_02_r2_c2_escrow_journal import (
    C2DurableEscrowJournal,
    ExternalHeadAnchorC2,
    make_crash_hook_for_phase,
)
from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture


def _snap(sid: str, tag: str = "") -> dict:
    s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=sid)
    s["dataset_sha256"] = hashlib.sha256(f"{sid}:{tag}".encode()).hexdigest()
    return s


def test_c3_attack_18_19_enospc_eio_blob_eintr_retry_and_short_write_fail_closed(tmp_path: Path) -> None:
    # Attack 18: ENOSPC & EIO during O_EXCL blob write preserve prior committed journal untouched
    for phase, exp_errno in (("ENOSPC_BLOB_WRITE", errno.ENOSPC), ("EIO_BLOB_WRITE", errno.EIO)):
        j = C2DurableEscrowJournal(tmp_path / f"err_{phase}")
        j.append_snapshot(_snap("base"))
        before = j.journal_path.read_bytes()
        with pytest.raises(OSError) as ei:
            j.append_snapshot(_snap("target"), _crash_hook=make_crash_hook_for_phase(phase))
        assert ei.value.errno == exp_errno
        assert j.journal_path.read_bytes() == before
        # Orphan partial blob must fail integrity until explicit quarantine
        assert j.verify_integrity()["valid"] is False
        q1 = j.quarantine_corrupt_tail_and_orphans(operator_reason=f"Q1_{phase}")
        assert q1["quarantined_count"] == 1
        assert q1["post_recovery_integrity"]["valid"] is True
        # Second recovery is cleanly idempotent (0 quarantined, prefix intact)
        q2 = j.quarantine_corrupt_tail_and_orphans(operator_reason=f"Q2_{phase}")
        assert q2["quarantined_count"] == 0
        assert q2["post_recovery_integrity"]["valid"] is True
        assert j.journal_path.read_bytes() == before

    # Attack 19: zero-progress short write fails closed; EINTR + partial write retries cleanly
    j_short = C2DurableEscrowJournal(tmp_path / "short_zero")
    with pytest.raises(IOError, match="SHORT_WRITE_ZERO_PROGRESS"):
        j_short.append_snapshot(_snap("s_zero"), _write_fn=lambda _fd, _b: 0)

    j_eintr = C2DurableEscrowJournal(tmp_path / "eintr_partial")
    state = {"calls": 0}

    def _flaky_writer(fd: int, data: bytes) -> int:
        state["calls"] += 1
        if state["calls"] in (1, 3):
            raise OSError(errno.EINTR, "SIMULATED_EINTR")
        return os.write(fd, data[: max(1, len(data) // 2)])

    res = j_eintr.append_snapshot(_snap("s_eintr"), _write_fn=_flaky_writer)
    assert res["action"] == "APPENDED_NEW_ENTRY"
    assert state["calls"] >= 4
    assert j_eintr.verify_integrity()["valid"] is True


def test_c3_attack_20_21_crash_before_anchor_pending_and_rollback_detection_no_worm(tmp_path: Path) -> None:
    # Attack 20: Crash after journal fsync BEFORE external anchor -> UNANCHORED_PENDING_ATTESTATION (NOT external-PASS!)
    anc = ExternalHeadAnchorC2(tmp_path / "anc_pending.jsonl")
    j = C2DurableEscrowJournal(tmp_path / "j_pending", external_anchor=anc)
    j.append_snapshot(_snap("a01"))
    assert j.verify_integrity()["rollback_detection_status"] == "EXTERNAL_ANCHOR_CONSISTENT"

    with pytest.raises(RuntimeError, match="AFTER_JOURNAL_FSYNC_BEFORE_ANCHOR"):
        j.append_snapshot(_snap("a02"), _crash_hook=make_crash_hook_for_phase("AFTER_JOURNAL_FSYNC_BEFORE_ANCHOR"))

    chk_pend = j.verify_integrity()
    assert chk_pend["valid"] is True
    assert chk_pend["record_count"] == 2
    assert chk_pend["rollback_detection_status"] == "UNANCHORED_PENDING_ATTESTATION"
    assert chk_pend["immutability_class"] == "LOCAL_COMMITTED_UNANCHORED_PENDING_ATTESTATION"
    assert chk_pend["external_anchor"]["head_attested"] is False
    assert chk_pend["worm_verified"] is False

    # Attack 21: Rollback below anchor detected immediately; never claims hardware WORM
    anc2 = ExternalHeadAnchorC2(tmp_path / "anc_rb.jsonl")
    j2 = C2DurableEscrowJournal(tmp_path / "j_rb", external_anchor=anc2)
    j2.append_snapshot(_snap("rb01"))
    j2.append_snapshot(_snap("rb02"))
    lines = j2.journal_path.read_bytes().split(b"\n")[:-1]
    j2.journal_path.write_bytes(lines[0] + b"\n")
    chk_rb = j2.verify_integrity()
    assert chk_rb["valid"] is False
    assert chk_rb["rollback_detection_status"] == "FAIL_ROLLBACK_DETECTED_BELOW_EXTERNAL_ANCHOR"
    assert chk_rb["worm_verified"] is False


def test_c3_attack_22_concurrent_2_4_8_and_3_os_proc_flock_plus_low_disk_abort(tmp_path: Path) -> None:
    # Low OS disk aborts inside exclusive lock before writing
    j_low = C2DurableEscrowJournal(tmp_path / "low_disk", os_disk_probe_fn=lambda: 15.0)
    with pytest.raises(RuntimeError, match="BLOCKED_LOW_DISK_IN_ESCROW_APPEND"):
        j_low.append_snapshot(_snap("ld01"))
    assert j_low.verify_integrity()["record_count"] == 0

    # Concurrent 2, 4, 8 threads exact-once + fork conflict
    for nw in (2, 4, 8):
        cj = C2DurableEscrowJournal(tmp_path / f"c_{nw}")
        bar = threading.Barrier(nw)
        same = _snap(f"same_{nw}", "s")
        with ThreadPoolExecutor(max_workers=nw) as pool:
            acts = list(pool.map(lambda _: (bar.wait(), cj.append_snapshot(same)["action"])[1], range(nw)))
        assert acts.count("APPENDED_NEW_ENTRY") == 1
        assert acts.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == nw - 1
        with pytest.raises(PermissionError, match="IMMUTABLE_REGISTRY_OVERWRITE_FORBIDDEN"):
            cj.append_snapshot(dict(same, bar_count=777))

    # 3 separate OS processes contending via fcntl.flock (1 pytest + 3 children = 4 <= GLOBAL_MAX_DESCENDANTS)
    mp_dir = tmp_path / "mp_c3_flock"
    code = "\n".join([
        "import hashlib, json, sys; from pathlib import Path",
        "from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal",
        "from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture",
        "j = C2DurableEscrowJournal(Path(sys.argv[1])); w = int(sys.argv[2]); res = []",
        "for k in range(2):",
        "    s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f'p{w}_k{k}')",
        "    s['dataset_sha256'] = hashlib.sha256(f'p{w}_k{k}'.encode()).hexdigest()",
        "    res.append(j.append_snapshot(s)['action'])",
        "sh = make_synthetic_valid_c1_snapshot_fixture(snapshot_id='p_shared')",
        "sh['dataset_sha256'] = 'd' * 64",
        "res.append(j.append_snapshot(sh)['action'])",
        "print(json.dumps(res))",
    ])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(mp_dir), str(i)], cwd=str(APP_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(3)
    ]
    d_acts, s_acts = [], []
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, err
        arr = json.loads(out.strip())
        d_acts.extend(arr[:2])
        s_acts.append(arr[2])
    assert d_acts.count("APPENDED_NEW_ENTRY") == 6
    assert s_acts.count("APPENDED_NEW_ENTRY") == 1
    assert s_acts.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 2
    chk = C2DurableEscrowJournal(mp_dir).verify_integrity()
    assert chk["valid"] is True and chk["record_count"] == 7
