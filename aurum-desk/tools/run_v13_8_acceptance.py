#!/usr/bin/env python3
"""
AURUM DESK — V13.8 VERIFIABLE ACCEPTANCE SCRIPT (Phần 88-91, 110-112, 143-146).
Executes all acceptance stages:
Stage 1: Backend Unit, Lifecycle, Idempotency, Costs, Target & Integrity Tests (pytest + JUnit XML)
Stage 2: Frontend Vitest & Production Build
Stage 3: Empirical 3-Month Replay with Causal Execution Pipeline
Stage 4: Cell-by-cell Artifact Verifier (report.json, trades.csv, Excel workbook)
Stage 5: Determinism Verification (Repeat replay on frozen dataset)
Outputs acceptance.json with verifiable evidence and exits 0 only if all mandatory technical checks PASS.
"""

import sys
import os
import subprocess
import json
import time
import hashlib
import argparse
import xml.etree.ElementTree as ET

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BACKEND_DIR = os.path.join(PROJECT_ROOT, 'backend')
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')

sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, PROJECT_ROOT)


def log(msg: str):
    print(f'[*] {msg}', flush=True)


def get_git_info() -> dict:
    try:
        head_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT_ROOT).decode().strip()
        status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=PROJECT_ROOT).decode().strip()
        diff = subprocess.check_output(['git', 'diff'], cwd=PROJECT_ROOT)
        diff_hash = hashlib.sha256(diff).hexdigest()[:16]
        return {
            'head_commit': head_commit,
            'diff_hash': diff_hash,
            'has_uncommitted_changes': bool(status)
        }
    except Exception as e:
        return {'head_commit': 'unknown', 'diff_hash': 'unknown', 'error': str(e)}


def parse_junit_xml(xml_path: str) -> dict:
    if not os.path.exists(xml_path):
        return {'total': 0, 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
        total = sum(int(s.attrib.get('tests', 0)) for s in suites)
        failures = sum(int(s.attrib.get('failures', 0)) for s in suites)
        errors = sum(int(s.attrib.get('errors', 0)) for s in suites)
        skipped = sum(int(s.attrib.get('skipped', 0)) for s in suites)
        passed = total - failures - errors - skipped
        return {
            'total': total,
            'passed': passed,
            'failed': failures,
            'errors': errors,
            'skipped': skipped
        }
    except Exception as e:
        return {'error': str(e), 'total': 0, 'passed': 0, 'failed': 0}


def run_stage_1_backend_tests() -> dict:
    log('STAGE 1: Running Backend Pytest Suites (V13.8 Lifecycle, Idempotency, Costs, Provenance, Driver, Audit, Integrity & E2E)...')
    t0 = time.time()
    test_files = [
        'backend/tests/test_v13_8_order_lifecycle.py',
        'backend/tests/test_v13_8_ledger_idempotency.py',
        'backend/tests/test_v13_8_execution_costs.py',
        'backend/tests/test_v13_8_target_provenance.py',
        'backend/tests/test_v13_8_causal_driver.py',
        'backend/tests/test_v13_8_data_audit.py',
        'backend/tests/test_v13_8_artifact_contract.py',
        'backend/tests/test_v13_8_cadence_contract.py',
        'backend/tests/test_v13_8_engine_e2e.py',
        'backend/tests/test_v13_7_evidence_utils.py'
    ]
    venv_pytest = os.path.join(BACKEND_DIR, 'venv', 'bin', 'pytest')
    pytest_bin = venv_pytest if os.path.exists(venv_pytest) else 'pytest'
    xml_out = os.path.join(BACKEND_DIR, 'lab', 'artifacts', 'v13_8_tests', 'junit.xml')
    os.makedirs(os.path.dirname(xml_out), exist_ok=True)

    cmd = [pytest_bin] + test_files + [f'--junitxml={xml_out}']
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{BACKEND_DIR}:{PROJECT_ROOT}'
    res = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
    duration = round(time.time() - t0, 2)

    passed = res.returncode == 0
    xml_counts = parse_junit_xml(xml_out)

    return {
        'status': 'PASS' if passed else 'FAIL',
        'mandatory': True,
        'passed': passed,
        'duration_seconds': duration,
        'test_files_count': len(test_files),
        'total_tests': xml_counts.get('total', 0),
        'passed_tests': xml_counts.get('passed', 0),
        'failed_tests': xml_counts.get('failed', 0),
        'junit_xml': xml_out,
        'stdout_snippet': res.stdout[-600:] if res.stdout else '',
        'stderr_snippet': res.stderr[-400:] if res.stderr else ''
    }


def run_stage_2_frontend_tests_and_build() -> dict:
    log('STAGE 2: Running Frontend Vitest & Production Build...')
    t0 = time.time()
    try:
        t_res = subprocess.run(['npm', 'test', '--', '--run'], cwd=FRONTEND_DIR, capture_output=True, text=True)
        t_pass = t_res.returncode == 0

        b_res = subprocess.run(['npm', 'run', 'build'], cwd=FRONTEND_DIR, capture_output=True, text=True)
        b_pass = b_res.returncode == 0

        duration = round(time.time() - t0, 2)
        passed = t_pass and b_pass
        return {
            'status': 'PASS' if passed else 'FAIL',
            'mandatory': True,
            'passed': passed,
            'vitest_passed': t_pass,
            'build_passed': b_pass,
            'duration_seconds': duration,
            'build_snippet': b_res.stdout[-300:] if b_res.stdout else ''
        }
    except Exception as e:
        return {'status': 'FAIL', 'mandatory': True, 'passed': False, 'error': str(e), 'duration_seconds': round(time.time() - t0, 2)}


def run_stage_3_empirical_3m_replay(run_name: str = 'v13_8_acceptance_empirical_3m') -> dict:
    log(f'STAGE 3: Running Empirical 3-Month Replay with Causal Execution Pipeline ({run_name})...')
    import schemas
    from lab.replay_engine import ReplayEngine
    from lab.replay_integrity import verify_exported_artifacts

    start_ts = 1783609200000
    end_ts = 1791558000000

    req = schemas.ReplayRunRequest(
        run_name=run_name,
        symbol='XAUUSDT',
        start_ts=start_ts,
        end_ts=end_ts,
        initial_equity=1000.0,
        risk_pct=0.5,
        quota_risk_pct=0.10,
        leverage=30,
        strategy_variant='NY_ADAPTIVE',
        entry_cadence='DAILY_PAPER',
        ny_max_fills=3,
        daily_min_fills_target=1,
        scheduled_deadline_hour=14,
        scheduled_deadline_minute=30,
        include_5m=True,
        use_5m_driver=True,
        mode='HISTORICAL_MARKET'
    )

    t0 = time.time()
    res = ReplayEngine.run_replay(req)
    duration = round(time.time() - t0, 2)

    # Verify artifacts cell-by-cell
    art_valid, art_errs = verify_exported_artifacts(res, res.artifacts_dir)

    is_comp = bool(res.total_trades > 0 and res.closed_count > 0)
    summary = {
        'status': 'PASS' if is_comp else 'FAIL',
        'mandatory': True,
        'passed': is_comp,
        'run_id': res.id,
        'run_name': res.run_name,
        'duration_seconds': duration,
        'total_trades': res.total_trades,
        'closed_count': res.closed_count,
        'open_positions_count': res.open_positions_count,
        'fills_count': res.fills_count,
        'wins': res.wins,
        'losses': res.losses,
        'breakevens': res.breakevens,
        'win_rate_pct': res.win_rate_pct,
        'profit_factor': res.profit_factor,
        'total_net_pnl': res.total_net_pnl,
        'total_fees': res.total_fees,
        'max_drawdown_usdt': res.max_drawdown_usdt,
        'max_drawdown_pct': res.max_drawdown_pct,
        'expectancy_r': res.expectancy_r,
        'trade_type_breakdown': res.trade_type_breakdown,
        'cadence_summary': res.cadence_summary,
        'run_config_hash': res.run_config_hash,
        'dataset_hash': res.dataset_hash,
        'integrity_summary': res.integrity_summary,
        'artifacts_dir': res.artifacts_dir,
        'artifacts_verified': art_valid,
        'artifacts_errors': art_errs
    }

    return summary


def run_stage_5_deterministic_repeat(run_a: dict) -> dict:
    log('STAGE 5: Running Determinism Repeat Stage (Re-running Replay on Same Frozen Config)...')
    t0 = time.time()
    run_b = run_stage_3_empirical_3m_replay(run_name='v13_8_acceptance_repeat_run_b')
    duration = round(time.time() - t0, 2)

    # Compare semantic fields
    mismatches = []
    for k in ['total_trades', 'closed_count', 'wins', 'losses', 'total_net_pnl', 'total_fees', 'dataset_hash', 'run_config_hash']:
        val_a = run_a.get(k)
        val_b = run_b.get(k)
        if val_a != val_b:
            mismatches.append(f'MISMATCH_{k}: runA={val_a}, runB={val_b}')

    cad_a = run_a.get('cadence_summary', {}).get('sessions_with_fills')
    cad_b = run_b.get('cadence_summary', {}).get('sessions_with_fills')
    if cad_a != cad_b:
        mismatches.append(f'MISMATCH_cadence_fills: runA={cad_a}, runB={cad_b}')

    passed = len(mismatches) == 0
    return {
        'status': 'PASS' if passed else 'FAIL',
        'mandatory': True,
        'passed': passed,
        'duration_seconds': duration,
        'run_a_id': run_a.get('run_id'),
        'run_b_id': run_b.get('run_id'),
        'mismatches': mismatches,
        'verified_deterministic': passed
    }


def derive_technical_status(stages: list) -> str:
    for s in stages:
        if s.get('status') == 'FAIL':
            return 'FAIL'
        if s.get('status') in ('SKIPPED', 'NOT_RUN', 'NOT_VERIFIED', 'BLOCKED'):
            return 'NOT_VERIFIED'
    return 'PASS'


def main():
    parser = argparse.ArgumentParser(description='Aurum Desk V13.8 Acceptance Runner')
    parser.add_argument('--skip-tests', action='store_true', help='Skip unit and frontend tests (marks status NOT_VERIFIED)')
    args = parser.parse_args()

    log('=== AURUM DESK V13.8 VERIFIABLE ACCEPTANCE RUNNER ===')
    overall_start = time.time()
    git_info = get_git_info()
    log('Git info: Base HEAD=' + str(git_info.get('head_commit')) + ', Diff Hash=' + str(git_info.get('diff_hash')))

    stages = []

    if not args.skip_tests:
        stage1 = run_stage_1_backend_tests()
        stages.append(stage1)
        if not stage1['passed']:
            log('STAGE 1 FAILED: ' + str(stage1.get('stdout_snippet')))
            sys.exit(1)
        log(f"STAGE 1 PASSED: All {stage1['total_tests']} tests in {stage1['test_files_count']} test suites passed in {stage1['duration_seconds']}s.")

        stage2 = run_stage_2_frontend_tests_and_build()
        stages.append(stage2)
        if not stage2['passed']:
            log('STAGE 2 FAILED: ' + str(stage2.get('error')))
            sys.exit(1)
        log('STAGE 2 PASSED: Frontend test suites passed & production bundle built in ' + str(stage2['duration_seconds']) + 's.')
    else:
        # PHẦN 144: --skip-tests marks status as SKIPPED, NOT_VERIFIED, mandatory=True
        stage1 = {'status': 'SKIPPED', 'mandatory': True, 'passed': False, 'reason': 'USER_DEBUG_SKIP'}
        stage2 = {'status': 'SKIPPED', 'mandatory': True, 'passed': False, 'reason': 'USER_DEBUG_SKIP'}
        stages.extend([stage1, stage2])

    empirical_res = run_stage_3_empirical_3m_replay()
    stages.append(empirical_res)
    integrity_ok = empirical_res.get('integrity_summary', {}).get('status') == 'PASS'
    artifacts_ok = empirical_res.get('artifacts_verified', False)

    stage4 = {
        'status': 'PASS' if (integrity_ok and artifacts_ok) else 'FAIL',
        'mandatory': True,
        'passed': integrity_ok and artifacts_ok,
        'integrity_passed': integrity_ok,
        'artifacts_verified': artifacts_ok,
        'artifacts_errors': empirical_res.get('artifacts_errors', [])
    }
    stages.append(stage4)

    stage5 = run_stage_5_deterministic_repeat(empirical_res)
    stages.append(stage5)

    log('Empirical Replay Complete (' + str(empirical_res['duration_seconds']) + 's):')
    log('  Closed trades: ' + str(empirical_res['closed_count']))
    log('  Net PnL: ' + str(empirical_res['total_net_pnl']) + ' USD')
    log('  Total Fees: ' + str(empirical_res['total_fees']) + ' USD')
    log('  Winrate: ' + str(empirical_res['win_rate_pct']) + '%')
    log('  Max Drawdown: ' + str(empirical_res['max_drawdown_pct']) + '%')
    cov = empirical_res.get('cadence_summary', {}).get('coverage_pct')
    fills_sess = empirical_res.get('cadence_summary', {}).get('sessions_with_fills')
    cad_status = empirical_res.get('cadence_summary', {}).get('cadence_status', 'UNMET')
    log('  Cadence Status: ' + str(cad_status) + ' (' + str(cov) + '% coverage, ' + str(fills_sess) + ' sessions with fills)')
    log('  Integrity Summary Status: ' + str(empirical_res.get('integrity_summary', {}).get('status')))
    log('  Artifacts Verified: ' + str(artifacts_ok))
    log('  Deterministic Repeat: ' + str(stage5.get('verified_deterministic')))

    technical_status = derive_technical_status(stages)
    log('DERIVED TECHNICAL STATUS: ' + technical_status)

    manifest = {
        'schema_version': 'v13.8',
        'timestamp': int(time.time() * 1000),
        'git_info': git_info,
        'stages': {
            'stage1_backend_tests': stage1,
            'stage2_frontend': stage2,
            'stage3_empirical_replay': empirical_res,
            'stage4_artifacts_and_integrity': stage4,
            'stage5_determinism': stage5
        },
        'technical_status': technical_status,
        'cadence_status': cad_status,
        'economic_status': 'PROFITABLE' if empirical_res['total_net_pnl'] > 0 else 'LOSING',
        'empirical_metrics': empirical_res,
        'elapsed_seconds': round(time.time() - overall_start, 2)
    }

    out_path = os.path.join(PROJECT_ROOT, 'acceptance.json')
    with open(out_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    log('Acceptance manifest written to: ' + out_path)

    if manifest['technical_status'] == 'PASS':
        log('=== ALL MANDATORY TECHNICAL CHECKS PASSED (RELEASE CANDIDATE READY) ===')
        sys.exit(0)
    else:
        log('=== MANDATORY TECHNICAL CHECKS FAILED OR NOT_VERIFIED ===')
        sys.exit(1)


if __name__ == '__main__':
    main()
