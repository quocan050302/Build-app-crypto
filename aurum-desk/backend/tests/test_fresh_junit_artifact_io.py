"""Real Windows writer-handle and independent JUnit tampering controls."""
import hashlib
import os

import pytest

from tools.fresh_junit_io import read_closed_junit_artifact
from tools.v17_9_3_1_2_7_proof_checks import verify_fresh_full_junit


def fixture_report(tmp_path):
    path=tmp_path/'fresh.xml'
    path.write_bytes(b'<testsuite><testcase classname="backend.tests.test_sample" name="test_ok"/></testsuite>')
    data,stat=read_closed_junit_artifact(path)
    shard=dict(shard_name='fresh',reused=False,tested_executable_sha='a'*40,pid=123,
               xml_mtime_ns=stat.st_mtime_ns,xml_sha256=hashlib.sha256(data).hexdigest(),
               pytest_exit_code=0,failed_count=0,error_count=0,exact_bijection=True,
               junit_xml_path=str(path))
    return path,dict(collected_node_ids=['backend/tests/test_sample.py::test_ok']),dict(
        shards=[shard],tested_executable_sha='a'*40,reused_shards_count=0,run_start_epoch_ns=stat.st_mtime_ns-1)


def test_closed_junit_capture_preserves_bytes_metadata_and_independent_verdict(tmp_path):
    path,collection,report=fixture_report(tmp_path)
    before=path.stat()
    assert verify_fresh_full_junit(tmp_path,collection,report)['status']=='PASS'
    after=path.stat()
    assert (before.st_mtime_ns,before.st_size)==(after.st_mtime_ns,after.st_size)


@pytest.mark.parametrize('mutation',['timestamp','content'])
def test_independent_junit_rejects_changes_after_capture(tmp_path,mutation):
    path,collection,report=fixture_report(tmp_path)
    if mutation=='timestamp':
        stat=path.stat()
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+2_000_000_000))
        reason='JUNIT_DISK_MTIME_MISMATCH'
    else:
        path.write_bytes(path.read_bytes().replace(b'test_ok',b'test_no'))
        reason='STALE_OR_TAMPERED_JUNIT'
    with pytest.raises(ValueError,match=reason):
        verify_fresh_full_junit(tmp_path,collection,report)


@pytest.mark.skipif(os.name!='nt',reason='Windows writer sharing semantics')
def test_capture_refuses_a_real_open_windows_writer_then_accepts_closed_file(tmp_path):
    path=tmp_path/'open_writer.xml'
    payload=b'<testsuite/>'
    with path.open('wb') as writer:
        writer.write(payload)
        writer.flush()
        with pytest.raises(RuntimeError,match='JUNIT_WRITER_STILL_OPEN'):
            read_closed_junit_artifact(path,timeout_seconds=.05)
    data,stat=read_closed_junit_artifact(path)
    assert data==payload
    assert stat.st_mtime_ns==path.stat().st_mtime_ns


@pytest.mark.skipif(os.name!='nt',reason='Windows writer sharing semantics')
def test_capture_waits_for_real_writer_to_close_before_attesting(tmp_path):
    import threading
    path=tmp_path/'pending_writer.xml'
    writer=path.open('wb')
    writer.write(b'<testsuite/>')
    writer.flush()
    close_writer=threading.Timer(.15,writer.close)
    close_writer.start()
    try:
        data,stat=read_closed_junit_artifact(path,timeout_seconds=2)
        assert writer.closed
        assert data==b'<testsuite/>'
        assert stat.st_mtime_ns==path.stat().st_mtime_ns
    finally:
        close_writer.join()
        writer.close()
