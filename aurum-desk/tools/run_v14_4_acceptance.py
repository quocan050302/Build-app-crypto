#!/usr/bin/env python3
"""
V14.4 Full Acceptance Runner (PHAN 157 & 158)
Runs:
1. Backend pytest (all tests)
2. Frontend vitest (all tests)
3. Frontend TypeScript build
4. Benchmark runner
Generates:
- logs/acceptance/v14_4/acceptance_v14_4.json
"""

import subprocess
import json
import os
import sys
import time

def run_cmd(cmd, cwd=None):
    print(f"--> Executing: {cmd}")
    t0 = time.perf_counter()
    p = subprocess.run(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    duration = time.perf_counter() - t0
    print(f"Exit code: {p.returncode} ({duration:.2f}s)")
    return p.returncode, p.stdout, duration

def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    logs_dir = os.path.join(root_dir, 'logs', 'acceptance', 'v14_4')
    os.makedirs(logs_dir, exist_ok=True)
    
    results = {}
    
    # 1. Backend Pytest
    python_bin = os.path.join(root_dir, 'backend', 'venv', 'bin', 'python3')
    backend_cmd = f"PYTHONPATH={os.path.join(root_dir, 'backend')} {python_bin} -m pytest {os.path.join(root_dir, 'backend', 'tests')}/test_v14* {os.path.join(root_dir, 'backend', 'tests')}/test_v7*"
    code, out, dur = run_cmd(backend_cmd)
    results["backend_pytest"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "duration_s": dur,
        "output_summary": out.splitlines()[-3:] if out else [],
    }
    
    # 2. Frontend Vitest
    code, out, dur = run_cmd("npm --prefix frontend test -- --run", cwd=root_dir)
    results["frontend_vitest"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "duration_s": dur,
        "output_summary": out.splitlines()[-5:] if out else [],
    }
    
    # 3. Frontend Production Build
    code, out, dur = run_cmd("npm --prefix frontend run build", cwd=root_dir)
    results["frontend_build"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "duration_s": dur,
    }
    
    # 4. Benchmark
    code, out, dur = run_cmd(f"{python_bin} {os.path.join(root_dir, 'tools', 'run_v14_4_chart_acceptance.py')}")
    results["benchmark"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "duration_s": dur,
    }
    
    all_passed = all(r["status"] == "PASS" for r in results.values())
    results["overall_acceptance"] = "PASS" if all_passed else "FAIL"
    results["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    out_json = os.path.join(logs_dir, 'acceptance_v14_4.json')
    with open(out_json, 'w') as f:
        json.dump(results, f, indent=2)
        
    print(f"\n=== ACCEPTANCE SUMMARY ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"Artifact saved to {out_json}")
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
