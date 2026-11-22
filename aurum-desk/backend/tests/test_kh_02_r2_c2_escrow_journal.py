"""
aurum-desk/backend/tests/test_kh_02_r2_c2_escrow_journal.py

AURUM DESK — KH-02.R2.C2 Unit & Integration Tests:
  T05: Append-only escrow journal, O_CREAT|O_EXCL blobs, idempotency, competing writers
       (2/4/8 threads + real 3-process OS fcntl.flock contention), no lost updates, C1 compat.
  T06: Crash injection at every phase (BEFORE_BLOB_WRITE, AFTER_BLOB_WRITE_BEFORE_FSYNC,
       FSYNC_FAILURE, AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND, TORN_JOURNAL_LINE,
       AFTER_JOURNAL_WRITE_BEFORE_FSYNC), fail-closed corruption & explicit quarantine recovery,
       symlink/path-escape/wrong-lock-owner/blob-tamper/missing-blob rejection.
  T07: Unanchored LOCAL_TAMPER_EVIDENT_ONLY (worm_verified=False) vs ExternalHeadAnchorC2
       rollback & fork tamper detection, plus read-only legacy C1 migration.
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

from services.kh_02_r2_c2_escrow_journal import (
    C2DurableEscrowJournal,
    ExternalHeadAnchorC2,
    make_crash_hook_for_phase,
)
from services.kh_02_r2_prospective_data_service import (
    ProspectiveHashLinkedEscrowRegistryR2C1,
    make_synthetic_valid_c1_snapshot_fixture,
)


def _snap(sid: str, seed_tag: str = "") -> dict:
    s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=sid)
    s["dataset_sha256"] = hashlib.sha256(f"{sid}:{seed_tag}".encode("utf-8")).hexdigest()
    return s


def test_t05_append_only_prior_bytes_untouched_idempotent_and_conflict(tmp_path: Path) -> None:
    j = C2DurableEscrowJournal(tmp_path / "escrow_basic")
    s1 = _snap("c2_t05_001")
    s2 = _snap("c2_t05_002")

    r1 = j.append_snapshot(s1)
    assert r1["action"] == "APPENDED_NEW_ENTRY"
    assert r1["seq_index"] == 0
    assert r1["journal_prior_bytes_untouched"] is True
    b1 = j.journal_path.read_bytes()
    ino1 = j.journal_path.stat().st_ino

    r2 = j.append_snapshot(s2)
    assert r2["action"] == "APPENDED_NEW_ENTRY"
    assert r2["seq_index"] == 1
    b2 = j.journal_path.read_bytes()
    ino2 = j.journal_path.stat().st_ino
    assert ino1 == ino2
    assert b2[: len(b1)] == b1
    assert len(b2) > len(b1)

    # Exact duplicate -> idempotent NOOP without growing journal
    r2_dup = j.append_snapshot(s2)
    assert r2_dup["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP"
    assert j.journal_path.read_bytes() == b2

    # Same snapshot_id with different content -> hard overwrite rejection
    s2_conflict = dict(s2, bar_count=int(s2["bar_count"]) + 10)
    with pytest.raises(PermissionError, match="IMMUTABLE_REGISTRY_OVERWRITE_FORBIDDEN"):
        j.append_snapshot(s2_conflict)
    assert j.journal_path.read_bytes() == b2

    chk = j.verify_integrity()
    assert chk["valid"] is True
    assert chk["record_count"] == 2
    assert chk["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY"
    assert chk["worm_verified"] is False


@pytest.mark.parametrize("n_writers", [2, 4, 8])
def test_t05_competing_writers_distinct_and_duplicate_exact_once(tmp_path: Path, n_writers: int) -> None:
    j = C2DurableEscrowJournal(tmp_path / f"escrow_conc_{n_writers}")

    # Phase A: N competing writers with distinct snapshot IDs -> 0 lost updates
    bar_a = threading.Barrier(n_writers)

    def _w_distinct(idx: int) -> str:
        sn = _snap(f"conc_d_{n_writers}_{idx:02d}", f"d_{idx}")
        bar_a.wait()
        return str(j.append_snapshot(sn)["action"])

    with ThreadPoolExecutor(max_workers=n_writers) as pool:
        acts_d = list(pool.map(_w_distinct, range(n_writers)))

    assert acts_d.count("APPENDED_NEW_ENTRY") == n_writers

    # Phase B: N competing writers racing on the EXACT same snapshot ID -> 1 APPENDED, N-1 NOOP
    bar_b = threading.Barrier(n_writers)
    same_sn = _snap(f"conc_same_{n_writers}", "shared")

    def _w_same(_idx: int) -> str:
        bar_b.wait()
        return str(j.append_snapshot(same_sn)["action"])

    with ThreadPoolExecutor(max_workers=n_writers) as pool:
        acts_s = list(pool.map(_w_same, range(n_writers)))

    assert acts_s.count("APPENDED_NEW_ENTRY") == 1
    assert acts_s.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == n_writers - 1

    chk = j.verify_integrity()
    assert chk["valid"] is True
    assert chk["record_count"] == n_writers + 1


def test_t05_real_interprocess_flock_contention_3_os_subprocesses(tmp_path: Path) -> None:
    """
    Spawns 3 separate OS processes (1 pytest + 3 children = 4 <= GLOBAL_MAX_DESCENDANTS)
    contending simultaneously on the same C2DurableEscrowJournal via OS fcntl.flock(LOCK_EX).
    """
    escrow_dir = tmp_path / "escrow_mp_flock"
    child_code = "\n".join([
        "import hashlib, json, sys",
        "from pathlib import Path",
        "from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal",
        "from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture",
        "j = C2DurableEscrowJournal(Path(sys.argv[1]))",
        "wid = int(sys.argv[2])",
        "outs = []",
        "for k in range(3):",
        "    s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f'mp_w{wid}_k{k}')",
        "    s['dataset_sha256'] = hashlib.sha256(f'mp_{wid}_{k}'.encode()).hexdigest()",
        "    outs.append(j.append_snapshot(s)['action'])",
        "same = make_synthetic_valid_c1_snapshot_fixture(snapshot_id='mp_shared_id')",
        "same['dataset_sha256'] = 'c' * 64",
        "outs.append(j.append_snapshot(same)['action'])",
        "print(json.dumps(outs))",
    ])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child_code, str(escrow_dir), str(wi)],
            cwd=str(APP_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for wi in range(3)
    ]
    shared_actions = []
    distinct_actions = []
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, f"child failed: {err}"
        arr = json.loads(out.strip())
        distinct_actions.extend(arr[:3])
        shared_actions.append(arr[3])

    assert distinct_actions.count("APPENDED_NEW_ENTRY") == 9
    assert shared_actions.count("APPENDED_NEW_ENTRY") == 1
    assert shared_actions.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 2

    chk = C2DurableEscrowJournal(escrow_dir).verify_integrity()
    assert chk["valid"] is True
    assert chk["record_count"] == 10


def test_t06_crash_injection_every_phase_and_explicit_quarantine_recovery(tmp_path: Path) -> None:
    # Phase 0: BEFORE_BLOB_WRITE -> clean rollback, no orphan blob, no corrupt tail
    j0 = C2DurableEscrowJournal(tmp_path / "crash_p0")
    j0.append_snapshot(_snap("p0_base"))
    with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_BEFORE_BLOB_WRITE"):
        j0.append_snapshot(_snap("p0_crash"), _crash_hook=make_crash_hook_for_phase("BEFORE_BLOB_WRITE"))
    chk0 = j0.verify_integrity()
    assert chk0["valid"] is True and chk0["record_count"] == 1

    # Phase 1a / 1b / 1c: orphan uncommitted blob left behind -> verify fails closed -> quarantine restores cleanly
    for phase, exc_cls in (
        ("AFTER_BLOB_WRITE_BEFORE_FSYNC", RuntimeError),
        ("FSYNC_FAILURE", OSError),
        ("AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND", RuntimeError),
    ):
        jp = C2DurableEscrowJournal(tmp_path / f"crash_{phase}")
        jp.append_snapshot(_snap(f"{phase}_base"))
        s_fail = _snap(f"{phase}_target")
        with pytest.raises(exc_cls):
            jp.append_snapshot(s_fail, _crash_hook=make_crash_hook_for_phase(phase))
        chk_bad = jp.verify_integrity()
        assert chk_bad["valid"] is False
        assert len(chk_bad["orphan_blobs"]) == 1
        with pytest.raises(RuntimeError, match="FAIL_CLOSED_CORRUPT_ESCROW_JOURNAL"):
            jp.append_snapshot(_snap(f"{phase}_blocked"))
        q = jp.quarantine_corrupt_tail_and_orphans(operator_reason=f"RECOVER_{phase}")
        assert q["quarantined_count"] == 1
        assert q["post_recovery_integrity"]["valid"] is True
        assert jp.append_snapshot(s_fail)["action"] == "APPENDED_NEW_ENTRY"
        assert jp.verify_integrity()["record_count"] == 2

    # Phase 2: TORN_JOURNAL_LINE -> partial line without newline -> fails closed -> quarantine preserves torn bytes + SHA256
    jt = C2DurableEscrowJournal(tmp_path / "crash_torn")
    jt.append_snapshot(_snap("torn_base"))
    s_torn = _snap("torn_target")
    with pytest.raises(RuntimeError, match="SIMULATED_CRASH_TORN_JOURNAL_LINE"):
        jt.append_snapshot(s_torn, _crash_hook=make_crash_hook_for_phase("TORN_JOURNAL_LINE"))
    chk_t = jt.verify_integrity()
    assert chk_t["valid"] is False
    assert chk_t["has_corrupt_tail"] is True
    assert len(chk_t["orphan_blobs"]) == 1
    with pytest.raises(RuntimeError, match="FAIL_CLOSED_CORRUPT_ESCROW_JOURNAL"):
        jt.append_snapshot(_snap("torn_blocked"))

    # Empty operator_reason must fail closed
    with pytest.raises(ValueError, match="EXPLICIT_OPERATOR_REASON_REQUIRED"):
        jt.quarantine_corrupt_tail_and_orphans(operator_reason="   ")

    qt = jt.quarantine_corrupt_tail_and_orphans(operator_reason="OWNER_QUARANTINE_TORN_TAIL")
    assert qt["quarantined_count"] == 2  # torn tail + orphan blob
    q_dir = Path(qt["quarantine_dir"])
    assert (q_dir / "corrupt_journal_tail.bin").is_file()
    assert (q_dir / "quarantine_receipt.json").is_file()
    assert qt["post_recovery_integrity"]["valid"] is True
    assert qt["post_recovery_integrity"]["record_count"] == 1
    assert jt.append_snapshot(s_torn)["action"] == "APPENDED_NEW_ENTRY"
    assert jt.verify_integrity()["valid"] is True

    # Phase 3: AFTER_JOURNAL_WRITE_BEFORE_FSYNC -> valid line written, recovery preserves committed prefix cleanly
    jf = C2DurableEscrowJournal(tmp_path / "crash_after_jwrite")
    jf.append_snapshot(_snap("jwrite_base"))
    with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_AFTER_JOURNAL_WRITE_BEFORE_FSYNC"):
        jf.append_snapshot(_snap("jwrite_s2"), _crash_hook=make_crash_hook_for_phase("AFTER_JOURNAL_WRITE_BEFORE_FSYNC"))
    chk_f = jf.verify_integrity()
    assert chk_f["valid"] is True
    assert chk_f["record_count"] == 2


def test_t06_security_controls_symlink_blob_tamper_missing_blob_and_wrong_lock_owner(tmp_path: Path) -> None:
    # Symlink escrow root rejected
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_escrow"
    sym_dir.symlink_to(real_dir)
    with pytest.raises(PermissionError, match="SYMLINK_ESCROW_ROOT_FORBIDDEN"):
        C2DurableEscrowJournal(sym_dir)

    # Unsafe snapshot ID rejected
    j = C2DurableEscrowJournal(tmp_path / "escrow_sec")
    bad_s = _snap("safe_id")
    bad_s["snapshot_id"] = "../escape_id"
    with pytest.raises(ValueError, match="UNSAFE_OR_INVALID_SNAPSHOT_ID"):
        j.append_snapshot(bad_s)

    # Tampered blob bytes detected on verify
    j.append_snapshot(_snap("sec_01"))
    blob_files = list(j.blobs_dir.glob("*.blob"))
    assert len(blob_files) == 1
    orig = blob_files[0].read_bytes()
    blob_files[0].write_bytes(orig + b"TAMPER")
    chk_tamper = j.verify_integrity()
    assert chk_tamper["valid"] is False
    assert any("BLOB_BYTES_SHA256_MISMATCH" in v for v in chk_tamper["violations"])

    # Missing blob detected on verify
    blob_files[0].unlink()
    chk_miss = j.verify_integrity()
    assert chk_miss["valid"] is False
    assert any("RECORD_WITHOUT_BLOB" in v for v in chk_miss["violations"])

    # Wrong lock owner rejected by exclusive_lock()
    j2 = C2DurableEscrowJournal(tmp_path / "escrow_lock_owner")
    with pytest.raises(PermissionError, match="WRONG_LOCK_OWNER"):
        with j2.exclusive_lock(expected_owner_pid=os.getpid() + 99999):
            pass


def test_t07_external_anchor_rollback_and_fork_detection_plus_readonly_c1_migration(tmp_path: Path) -> None:
    # Unanchored journal explicitly discloses LOCAL_TAMPER_EVIDENT_ONLY (never claims WORM)
    j_un = C2DurableEscrowJournal(tmp_path / "unanchored")
    j_un.append_snapshot(_snap("un_01"))
    chk_un = j_un.verify_integrity()
    assert chk_un["valid"] is True
    assert chk_un["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY"
    assert chk_un["worm_verified"] is False
    assert chk_un["rollback_detection_status"] == "ROLLBACK_NOT_DETECTABLE_WITHOUT_EXTERNAL_ANCHOR"

    # External anchor detects BOTH rollback (truncation to older valid prefix) AND fork tamper!
    anc = ExternalHeadAnchorC2(tmp_path / "external_anchor.jsonl")
    ja = C2DurableEscrowJournal(tmp_path / "anchored", external_anchor=anc)
    ja.append_snapshot(_snap("anc_01"))
    ja.append_snapshot(_snap("anc_02"))
    chk_ok = ja.verify_integrity()
    assert chk_ok["valid"] is True
    assert chk_ok["rollback_detection_status"] == "EXTERNAL_ANCHOR_CONSISTENT"
    assert chk_ok["worm_verified"] is False  # Still local disk, honest trust tier!

    # Rollback to 1-record valid prefix -> detected immediately by external anchor!
    lines = ja.journal_path.read_bytes().split(b"\n")[:-1]
    ja.journal_path.write_bytes(lines[0] + b"\n")
    chk_rb = ja.verify_integrity()
    assert chk_rb["valid"] is False
    assert chk_rb["rollback_detection_status"] == "FAIL_ROLLBACK_DETECTED_BELOW_EXTERNAL_ANCHOR"
    assert any("JOURNAL_ROLLBACK_DETECTED" in v for v in chk_rb["violations"])

    # Legacy C1 registry upgrade: prior bytes untouched on append AND read-only import leaves source untouched
    leg_path = tmp_path / "legacy_c1.jsonl"
    leg_reg = ProspectiveHashLinkedEscrowRegistryR2C1(leg_path)
    leg_reg.append_validated_snapshot(_snap("leg_01"))
    b_leg_1 = leg_path.read_bytes()
    ino_leg_1 = leg_path.stat().st_ino
    leg_reg.append_validated_snapshot(_snap("leg_02"))
    b_leg_2 = leg_path.read_bytes()
    assert leg_path.stat().st_ino == ino_leg_1
    assert b_leg_2[: len(b_leg_1)] == b_leg_1
    sha_before = hashlib.sha256(b_leg_2).hexdigest()

    mig = C2DurableEscrowJournal(tmp_path / "migrated_c2")
    ref = mig.import_legacy_c1_registry_readonly(leg_path)
    assert ref["legacy_c1_record_count"] == 2
    assert ref["legacy_c1_source_mutated"] is False
    assert hashlib.sha256(leg_path.read_bytes()).hexdigest() == sha_before
    # Re-importing exact same source is idempotent
    ref2 = mig.import_legacy_c1_registry_readonly(leg_path)
    assert ref2["legacy_c1_source_sha256"] == sha_before
