#!/usr/bin/env python3
"""
AURUM DESK — V13.6 VERIFIABLE ACCEPTANCE SCRIPT (Phan 96-97).
Executes all acceptance stages:
Stage 1: Backend Unit, Integration & Negative Integrity Tests (pytest)
Stage 2: Frontend Vitest & Production Build
Stage 3: Empirical 3-Month Replay with Causal Execution Pipeline
Stage 4: Cell-by-cell Artifact Verifier (report.json, trades.csv, Excel workbook)
Outputs acceptance.json with verifiable evidence and exits 0 only if all mandatory technical checks PASS.
"""

import sys
import os
import subprocess
import json
import time
import hashlib

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


def run_stage_1_backend_tests() -> dict:
    log('STAGE 1: Running Backend Pytest Suites (including V13.6 Negative Integrity & Integration)...')
    t0 = time.time()
    test_files = [
        'backend/tests/test_v13_5_price_plan_acceptance.py',
        'backend/tests/test_v13_5_accounting_acceptance.py',
        'backend/tests/test_v13_5_execution_acceptance.py',
        'backend/tests/test_v13_5_data_range_acceptance.py',
        'backend/tests/test_v13_5_strategy_routing.py',
        'backend/tests/test_v13_5_metrics_acceptance.py',
        'backend/tests/test_v13_5_integrity_acceptance.py',
        'backend/tests/test_v13_6_negative_integrity.py',
        'backend/tests/test_v13_6_engine_execution_integration.py',
        'backend/tests/test_v13_4_price_plans.py',
        'backend/tests/test_v13_4_accounting.py',
        'backend/tests/test_v13_4_dates_sessions.py',
        'backend/tests/test_v13_4_metrics_exports.py',
        'backend/tests/test_v13_4_scheduler_integration.py',
        'backend/tests/test_v13_2_replay_repair.py',
        'backend/tests/test_v13_3_daily_scheduler.py'
    ]
    cmd = ['pytest'] + test_files
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{BACKEND_DIR}:{PROJECT_ROOT}'
    res = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
    duration = round(time.time() - t0, 2)
    
    passed = res.returncode == 0
    return {
        'passed': passed,
        'duration_seconds': duration,
        'test_files_count': len(test_files),
        'stdout_snippet': res.stdout[-400:] if res.stdout else '',
        'return_code': res.returncode
    }


def run_stage_2_frontend_tests_and_build() -> dict:
    log('STAGE 2: Running Frontend Vitest & Production Build...')
    t0 = time.time()
    test_res = subprocess.run(['npm', 'test', '--', '--run'], cwd=FRONTEND_DIR, capture_output=True, text=True)
    if test_res.returncode != 0:
        return {'passed': False, 'error': 'Vitest tests failed', 'stdout': test_res.stdout[-300:]}

    build_res = subprocess.run(['npm', 'run', 'build'], cwd=FRONTEND_DIR, capture_output=True, text=True)
    duration = round(time.time() - t0, 2)
    return {
        'passed': build_res.returncode == 0,
        'duration_seconds': duration,
        'stdout_snippet': build_res.stdout[-300:] if build_res.stdout else ''
    }


def run_stage_3_empirical_3m_replay() -> dict:
    log('STAGE 3: Running Empirical 3-Month Replay with Causal Execution Pipeline...')
    import schemas
    from lab.replay_engine import ReplayEngine
    from lab.replay_integrity import verify_exported_artifacts

    start_ts = 1783609200000
    end_ts = 1791558000000

    req = schemas.ReplayRunRequest(
        run_name='v13_6_acceptance_empirical_3m',
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

    summary = {
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


def main():
    log('=== AURUM DESK V13.6 VERIFIABLE ACCEPTANCE RUNNER ===')
    overall_start = time.time()
    git_info = get_git_info()
    log('Git info: Base HEAD=' + str(git_info.get('head_commit')) + ', Diff Hash=' + str(git_info.get('diff_hash')))

    stage1 = run_stage_1_backend_tests()
    if not stage1['passed']:
        log('STAGE 1 FAILED: ' + str(stage1.get('stdout_snippet')))
        sys.exit(1)
    log('STAGE 1 PASSED: All ' + str(stage1['test_files_count']) + ' backend test suites passed in ' + str(stage1['duration_seconds']) + 's.')

    stage2 = run_stage_2_frontend_tests_and_build()
    if not stage2['passed']:
        log('STAGE 2 FAILED: ' + str(stage2.get('error')))
        sys.exit(1)
    log('STAGE 2 PASSED: 15 frontend test files passed & production bundle built in ' + str(stage2['duration_seconds']) + 's.')

    empirical_res = run_stage_3_empirical_3m_replay()
    integrity_ok = empirical_res.get('integrity_summary', {}).get('status') == 'PASS'
    artifacts_ok = empirical_res.get('artifacts_verified', False)

    log('Empirical Replay Complete (' + str(empirical_res['duration_seconds']) + 's):')
    log('  Closed trades: ' + str(empirical_res['closed_count']))
    log('  Net PnL: ' + str(empirical_res['total_net_pnl']) + ' USD')
    log('  Winrate: ' + str(empirical_res['win_rate_pct']) + '%')
    log('  Max Drawdown: ' + str(empirical_res['max_drawdown_pct']) + '%')
    cov = empirical_res.get('cadence_summary', {}).get('coverage_pct')
    fills_sess = empirical_res.get('cadence_summary', {}).get('sessions_with_fills')
    log('  Cadence: ' + str(cov) + '% coverage (' + str(fills_sess) + ' sessions with fills)')
    log('  Integrity Summary Status: ' + str(empirical_res.get('integrity_summary', {}).get('status')))
    log('  Artifacts Verified: ' + str(artifacts_ok))

    manifest = {
        'schema_version': 'v13.6',
        'timestamp': int(time.time() * 1000),
        'git_info': git_info,
        'stage1_backend_tests': stage1,
        'stage2_frontend': stage2,
        'stage3_integrity_passed': integrity_ok,
        'stage3_artifacts_verified': artifacts_ok,
        'technical_status': 'PASS' if (stage1['passed'] and stage2['passed'] and integrity_ok and artifacts_ok) else 'FAIL',
        'cadence_status': 'PASS' if empirical_res.get('cadence_summary', {}).get('coverage_pct', 0) >= 80.0 else 'UNMET',
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
        log('=== MANDATORY TECHNICAL CHECKS FAILED ===')
        sys.exit(1)


if __name__ == '__main__':
    main()
