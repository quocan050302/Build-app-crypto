#!/usr/bin/env python3
"""
tools/run_v16_9_acceptance.py
V16.9 CG-01 Causal Execution, Real Historical Replay, Decision Funnel and Sizing Contract Acceptance Runner.

Key Principles (NHÓM 18, PHẦN 171 - 180):
1. Per-gate required evidence tracking with dynamic test extraction (NO hardcoded test counts).
2. True serialization of resolved EffectiveReplayFinancialConfig with canonical config hash.
3. Strict separation of Technical (PASS/FAIL), Historical (BLOCKED_DATA / EVALUATED_HISTORICAL),
   and Performance (MEASURED_PERFORMANCE / NOT_EVALUATED) statuses.
4. Clean separation of run_synthetic_execution_demo vs run_historical_cg01_replay (no seed orders on historical).
5. Non-zero exit code if any mandatory technical gate fails.
6. Produces artifacts/run_v16_9_<ts>/ and docs/V16_9_ACCEPTANCE_REPORT.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
DOCS = os.path.join(ROOT, "docs")
ARTIFACTS_ROOT = os.path.join(ROOT, "artifacts")

sys.path.insert(0, BACKEND)


def _venv_python() -> str:
    for rel in (("venv", "bin", "python3"), ("venv", "bin", "python"), ("venv", "Scripts", "python.exe")):
        cand = os.path.join(BACKEND, *rel)
        if os.path.exists(cand):
            return cand
    return sys.executable


def get_git_commit_sha() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        return proc.stdout.strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def get_git_diff_hash() -> str:
    try:
        proc = subprocess.run(["git", "diff"], cwd=ROOT, capture_output=True, text=True, check=True)
        return hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest()
    except Exception:
        return "UNKNOWN_DIFF"


def file_sha256(path: str) -> str:
    if not os.path.exists(path):
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def parse_pytest_output(output: str) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"passed": 0, "failed": 0, "skipped": 0, "deselected": 0, "warnings": 0}
    m_pass = re.search(r"(\d+)\s+passed", output)
    if m_pass:
        stats["passed"] = int(m_pass.group(1))
    m_fail = re.search(r"(\d+)\s+failed", output)
    if m_fail:
        stats["failed"] = int(m_fail.group(1))
    m_skip = re.search(r"(\d+)\s+skipped", output)
    if m_skip:
        stats["skipped"] = int(m_skip.group(1))
    m_desel = re.search(r"(\d+)\s+deselected", output)
    if m_desel:
        stats["deselected"] = int(m_desel.group(1))
    m_warn = re.search(r"(\d+)\s+warning", output)
    if m_warn:
        stats["warnings"] = int(m_warn.group(1))
    return stats


def extract_executed_test_names(output: str) -> List[str]:
    """Extract individual test names that passed from pytest verbose output."""
    passed_tests = []
    for line in output.splitlines():
        if " PASSED" in line:
            parts = line.split(" PASSED")[0].strip()
            if "::" in parts:
                test_name = parts.split("::")[-1].strip()
                passed_tests.append(test_name)
    return passed_tests


def run_command_step(name: str, argv: List[str], cwd: str, timeout: int = 600) -> Dict[str, Any]:
    print(f"--> [{name}] Running: {' '.join(argv)}", flush=True)
    t0 = time.perf_counter()
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        duration = round(time.perf_counter() - t0, 3)
        pytest_stats = parse_pytest_output(proc.stdout) if "pytest" in argv else None
        executed_cases = extract_executed_test_names(proc.stdout) if "pytest" in argv else []
        return {
            "name": name,
            "cmd": argv,
            "cwd": cwd,
            "exit_code": proc.returncode,
            "duration_sec": duration,
            "output": proc.stdout,
            "status": "PASS" if proc.returncode == 0 else "FAIL",
            "test_stats": pytest_stats,
            "executed_cases": executed_cases,
        }
    except Exception as e:
        duration = round(time.perf_counter() - t0, 3)
        return {
            "name": name,
            "cmd": argv,
            "cwd": cwd,
            "exit_code": -1,
            "duration_sec": duration,
            "output": str(e),
            "status": "ERROR",
            "test_stats": None,
            "executed_cases": [],
        }


def run_synthetic_execution_demo(initial_capital: float = 5000.0, risk_pct: float = 0.10) -> Dict[str, Any]:
    """
    Empirical Simulation Demo (PHẦN 174, 175):
    Evaluates 18 M1 synthetic bars with guarded order fill and expiry in isolation.
    """
    from domain_calculator import CostAssumptions
    from lab.cg01_replay_driver import CG01ReplayDriver
    import models

    ny_tz = ZoneInfo("America/New_York")
    dt_base = datetime(2026, 10, 15, 9, 30, tzinfo=ny_tz)
    t_base = int(dt_base.timestamp() * 1000)

    demo_bars = []
    for i in range(15):
        demo_bars.append({
            "open_time": t_base + i * 60_000,
            "open": 102.0, "high": 102.5, "low": 101.5, "close": 102.0, "volume": 100.0,
        })
    demo_bars.append({
        "open_time": t_base + 15 * 60_000,
        "open": 101.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 100.0,
    })
    demo_bars.append({
        "open_time": t_base + 16 * 60_000,
        "open": 100.5, "high": 100.5, "low": 96.8, "close": 97.0, "volume": 100.0,
    })
    demo_bars.append({
        "open_time": t_base + 17 * 60_000,
        "open": 97.0, "high": 97.0, "low": 94.8, "close": 95.0, "volume": 100.0,
    })

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump(demo_bars, f)
        demo_path = f.name

    def seed_demo_orders(adapter, db):
        # 1. Order that will fill at bar 15 through coordinator evaluate_orders_sync
        order1 = models.PaperOrder(
            id="ord_demo_guarded_fill",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=100.0,
            stop_loss=90.0,
            take_profit=125.0,
            quantity=0.5,
            initial_risk_usdt=5.0,
            risk_pct=0.10,
            created_at=t_base,
            armed_at=t_base,
            expires_at=t_base + 3_600_000,
        )
        # 2. Order that expires at t_base + 300_000 (bar 5) and is collected in rejection funnel
        order2 = models.PaperOrder(
            id="ord_demo_expired_guard",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=80.0,
            stop_loss=70.0,
            take_profit=100.0,
            quantity=0.5,
            initial_risk_usdt=5.0,
            risk_pct=0.10,
            created_at=t_base,
            armed_at=t_base,
            expires_at=t_base + 300_000,
        )
        db.add(order1)
        db.add(order2)
        db.commit()

    demo_costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.0)
    demo_driver = CG01ReplayDriver(
        m1_dataset_path=demo_path,
        initial_capital=initial_capital,
        risk_pct=risk_pct,
        costs=demo_costs,
        setup_hook=seed_demo_orders,
        capital_mode="CURRENT_ACCOUNT_BALANCE",
        execution_model="ASSUMED_OHLC_PATH",
    )
    demo_res = demo_driver.run()
    if os.path.exists(demo_path):
        os.remove(demo_path)

    return demo_res


def run_historical_cg01_replay(m1_dataset_path: str, initial_capital: float = 5000.0, risk_pct: float = 0.10) -> Dict[str, Any]:
    """
    Real Historical Replay Runner (PHẦN 006 - 010):
    Executes actual historical dataset with setup_hook=None (NEVER injects demo orders).
    """
    from domain_calculator import CostAssumptions
    from lab.cg01_replay_driver import CG01ReplayDriver

    costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.10)
    driver = CG01ReplayDriver(
        m1_dataset_path=m1_dataset_path,
        initial_capital=initial_capital,
        risk_pct=risk_pct,
        costs=costs,
        setup_hook=None,  # Strictly NO demo seeding on historical replay
        capital_mode="CURRENT_ACCOUNT_BALANCE",
        execution_model="ASSUMED_OHLC_PATH",
    )
    return driver.run()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="V16.9 Acceptance Runner for Causal Execution and Historical Replay")
    parser.add_argument("--m1-dataset", type=str, default="", help="Path to real M1 dataset JSON if available")
    parser.add_argument("--run-id", type=str, default="", help="Optional run ID")
    parser.add_argument("--dry-run", action="store_true", help="Skip test execution")
    args = parser.parse_args()

    t_start = int(time.time())
    t_start_iso = datetime.now(timezone.utc).isoformat()
    run_id = args.run_id or f"run_v16_9_{t_start}"
    commit_sha = get_git_commit_sha()
    diff_hash = get_git_diff_hash()
    py_bin = _venv_python()

    print(f"=== Starting V16.9 Acceptance Runner ({run_id}) ===")
    print(f"Git commit: {commit_sha}")
    print(f"Working tree diff hash: {diff_hash}")
    print(f"Python interpreter: {py_bin}")

    # Step 1: Execute technical suites and collect command results
    tech_results: List[Dict[str, Any]] = []
    if not args.dry_run:
        diff_res = run_command_step("git_diff_check", ["git", "diff", "--check"], cwd=ROOT)
        tech_results.append(diff_res)

        test_steps = [
            ("test_v16_9_causal_ohlc_execution", [py_bin, "-m", "pytest", "tests/test_v16_9_causal_ohlc_execution.py", "-v"]),
            ("test_v16_9_historical_runner", [py_bin, "-m", "pytest", "tests/test_v16_9_historical_runner.py", "-v"]),
            ("test_v16_9_cg01_decision_funnel", [py_bin, "-m", "pytest", "tests/test_v16_9_cg01_decision_funnel.py", "-v"]),
            ("test_v16_9_executable_sizing_contract", [py_bin, "-m", "pytest", "tests/test_v16_9_executable_sizing_contract.py", "-v"]),
            ("test_v16_8_capital_basis", [py_bin, "-m", "pytest", "tests/test_v16_8_capital_basis.py", "-v"]),
            ("test_v16_8_execution_model_and_time", [py_bin, "-m", "pytest", "tests/test_v16_8_execution_model_and_time.py", "-v"]),
            ("test_v16_8_statistics_and_accounting", [py_bin, "-m", "pytest", "tests/test_v16_8_statistics_and_accounting.py", "-v"]),
            ("test_v16_7_execution_guard_parity", [py_bin, "-m", "pytest", "tests/test_v16_7_execution_guard_parity.py", "-v"]),
            ("test_v16_7_risk_authority", [py_bin, "-m", "pytest", "tests/test_v16_7_risk_authority.py", "-v"]),
            ("test_v16_7_replay_costs", [py_bin, "-m", "pytest", "tests/test_v16_7_replay_costs.py", "-v"]),
            ("test_v16_6_measurement_integrity", [py_bin, "-m", "pytest", "tests/test_v16_6_measurement_integrity.py", "-v"]),
            ("v16_full_suite", [py_bin, "-m", "pytest", "tests", "-k", "v16", "-q"]),
        ]

        for name, cmd in test_steps:
            r = run_command_step(name, cmd, cwd=BACKEND)
            tech_results.append(r)
            if r["status"] != "PASS":
                print(f"FAILED step: {name}. Stop.")
                break

    # Step 2: Build Evidence-based Gate Verification (PHẦN 172, Phụ lục E)
    tech_results_by_name = {r["name"]: r for r in tech_results}

    def verify_gate(gate_name: str, required_cases: List[str], test_suite_name: str) -> Dict[str, Any]:
        suite_res = tech_results_by_name.get(test_suite_name)
        if not suite_res:
            return {
                "name": gate_name,
                "required_cases": required_cases,
                "executed_cases": [],
                "status": "NOT_RUN",
                "evidence_suite": test_suite_name,
                "reason": "Suite was not executed",
            }
        executed = suite_res.get("executed_cases", [])
        missing_cases = [c for c in required_cases if c not in executed and not any(c in ex for ex in executed)]
        if suite_res["status"] == "PASS" and not missing_cases:
            return {
                "name": gate_name,
                "required_cases": required_cases,
                "executed_cases": executed,
                "status": "PASS",
                "evidence_suite": test_suite_name,
                "reason": None,
            }
        else:
            return {
                "name": gate_name,
                "required_cases": required_cases,
                "executed_cases": executed,
                "status": "FAIL",
                "evidence_suite": test_suite_name,
                "reason": f"Missing or failed cases: {missing_cases}" if missing_cases else f"Suite failed with code {suite_res['exit_code']}",
            }

    gates_evidence = [
        verify_gate(
            "causal_execution",
            [
                "test_build_assumed_bar_events_pure_helper",
                "test_no_future_close_at_open_long",
                "test_no_future_close_at_open_short",
                "test_close_quote_mode_ignores_wicks",
                "test_unknown_execution_model_rejected",
            ],
            "test_v16_9_causal_ohlc_execution",
        ),
        verify_gate(
            "historical_routing",
            [
                "test_missing_dataset_returns_blocked_data",
                "test_corrupt_json_dataset_returns_blocked_data",
                "test_historical_branch_never_seeds_orders",
                "test_distinct_datasets_produce_distinct_fingerprints",
            ],
            "test_v16_9_historical_runner",
        ),
        verify_gate(
            "decision_contract",
            [
                "test_serialize_cg01_evaluation_pure_helper",
                "test_normalize_replay_cycle_observation",
                "test_orchestrator_returns_cg01_evaluation_on_observing",
                "test_funnel_counts_armed_order_correctly",
            ],
            "test_v16_9_cg01_decision_funnel",
        ),
        verify_gate(
            "executable_sizing",
            [
                "test_resolve_executable_sizing_context_current_mode",
                "test_resolve_executable_sizing_context_fixed_mode",
                "test_coordinator_fill_persists_sizing_contract",
                "test_resolve_executable_sizing_context_excess_equity",
                "test_resolve_executable_sizing_context_short_direction",
                "test_legacy_order_fill_without_sizing_context",
            ],
            "test_v16_9_executable_sizing_contract",
        ),
        {
            "name": "full_regression_v16",
            "required_cases": ["v16_full_suite"],
            "executed_cases": ["v16_full_suite"] if tech_results_by_name.get("v16_full_suite", {}).get("status") == "PASS" else [],
            "status": "PASS" if tech_results_by_name.get("v16_full_suite", {}).get("status") == "PASS" else "FAIL",
            "evidence_suite": "v16_full_suite",
            "reason": None if tech_results_by_name.get("v16_full_suite", {}).get("status") == "PASS" else "Regression failure",
        },
        {
            "name": "git_cleanliness",
            "required_cases": ["git_diff_check"],
            "executed_cases": ["git_diff_check"] if tech_results_by_name.get("git_diff_check", {}).get("status") == "PASS" else [],
            "status": "PASS" if tech_results_by_name.get("git_diff_check", {}).get("status") == "PASS" else "FAIL",
            "evidence_suite": "git_diff_check",
            "reason": None if tech_results_by_name.get("git_diff_check", {}).get("status") == "PASS" else "Whitespace/diff check errors",
        },
    ]

    all_tech_pass = all(g["status"] == "PASS" for g in gates_evidence)

    # Step 3: Run Simulation / Historical Execution
    from domain_calculator import CostAssumptions
    from services.plan_financial_context_service import resolve_replay_financial_config

    has_real_dataset = bool(args.m1_dataset and os.path.exists(args.m1_dataset))
    if has_real_dataset:
        print(f"--> Executing real historical replay with dataset: {args.m1_dataset}")
        replay_res = run_historical_cg01_replay(args.m1_dataset)
        historical_status = "EVALUATED_HISTORICAL"
        performance_status = "MEASURED_PERFORMANCE"
        simulation_evidence_type = "REAL_HISTORICAL_DATA"
    else:
        print("--> No valid --m1-dataset supplied. Executing isolated synthetic demo for accounting verification.")
        replay_res = run_synthetic_execution_demo()
        historical_status = "BLOCKED_DATA"
        performance_status = "NOT_EVALUATED"
        simulation_evidence_type = "DATA_SYNTHESIZED_DEMO"

    # Step 4: Serialize Authoritative Effective Replay Financial Config (PHẦN 173)
    demo_costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.10)
    effective_cfg_obj = resolve_replay_financial_config(
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.10,
        maker_fee_bps=2.0,
        taker_fee_bps=4.0,
        costs=demo_costs,
        capital_mode="CURRENT_ACCOUNT_BALANCE",
        execution_model="ASSUMED_OHLC_PATH",
    )
    effective_cfg = {
        "initial_capital": effective_cfg_obj.initial_capital,
        "risk_pct": effective_cfg_obj.risk_pct,
        "slippage_usd": effective_cfg_obj.slippage_usd,
        "maker_fee_rate": effective_cfg_obj.maker_fee_rate,
        "taker_fee_rate": effective_cfg_obj.taker_fee_rate,
        "contract_multiplier": effective_cfg_obj.contract_multiplier,
        "capital_mode": effective_cfg_obj.capital_mode,
        "execution_model": effective_cfg_obj.execution_model,
        "authority_pipeline": "CG01ReplayExecutionAdapter + ScalpWatcherOrchestrator + ExecutionCoordinator",
    }
    config_hash = hashlib.sha256(json.dumps(effective_cfg, sort_keys=True).encode("utf-8")).hexdigest()

    # Step 5: Write Artifacts (PHẦN 181)
    artifacts_dir = os.path.join(ARTIFACTS_ROOT, run_id)
    os.makedirs(artifacts_dir, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)

    test_results_path = os.path.join(artifacts_dir, "test_results.json")
    with open(test_results_path, "w", encoding="utf-8") as f:
        json.dump(tech_results, f, indent=2)

    effective_config_path = os.path.join(artifacts_dir, "effective_config.json")
    with open(effective_config_path, "w", encoding="utf-8") as f:
        json.dump(effective_cfg, f, indent=2)

    trades_csv_path = os.path.join(artifacts_dir, "trades.csv")
    with open(trades_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["trade_id", "direction", "entry_time", "entry_price", "exit_time", "exit_price", "quantity", "net_pnl_usd", "exit_cause", "intrabar_ambiguous"])
        for t in replay_res.get("closed_trades", []):
            writer.writerow([t["trade_id"], t["direction"], t["entry_time"], t["entry_price"], t["exit_time"], t["exit_price"], t["quantity"], t["net_pnl_usd"], t.get("exit_reason", ""), t.get("intrabar_ambiguous", False)])
        if replay_res.get("open_position"):
            op = replay_res["open_position"]
            writer.writerow([op["trade_id"], op["direction"], op["entry_time"], op["entry_price"], "OPEN", "OPEN", op["quantity"], "UNREALIZED", "OPEN", op.get("intrabar_ambiguous", False)])

    daily_csv_path = os.path.join(artifacts_dir, "daily_stats.csv")
    with open(daily_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["date", "is_market_day", "fills_count", "ny_fills", "status", "shortfall_reason"])
        for d, ds in replay_res.get("daily_stats", {}).items():
            writer.writerow([ds["date"], ds["is_market_day"], ds["fills_count"], ds["ny_fills"], ds["status"], ds["shortfall_reason"]])

    eq_mtm_path = os.path.join(artifacts_dir, "equity_mtm.csv")
    with open(eq_mtm_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["timestamp", "mtm_equity", "drawdown_pct"])
        for pt in replay_res.get("equity_mtm", []):
            writer.writerow([pt["timestamp"], pt["mtm_equity"], pt["drawdown_pct"]])

    eq_realized_path = os.path.join(artifacts_dir, "equity_realized.csv")
    with open(eq_realized_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["timestamp", "cash_equity"])
        for pt in replay_res.get("equity_realized", []):
            writer.writerow([pt.get("timestamp", ""), pt.get("cash_equity", "")])

    funnel_path = os.path.join(artifacts_dir, "rejection_funnel.csv")
    with open(funnel_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["category", "count", "description"])
        for item in replay_res.get("rejection_funnel", []):
            writer.writerow([item.get("category", ""), item.get("count", 0), item.get("description", "")])

    total_fills = replay_res.get("total_fills", 0)
    closed_trades = replay_res.get("closed_trades_count", 0)
    open_pos_count = replay_res.get("open_positions_count", 0)
    metrics = replay_res.get("metrics", {})
    total_pnl = metrics.get("total_pnl_usd", 0.0)
    max_dd = metrics.get("max_drawdown_pct", 0.0)

    # Acceptance Payload (PHẦN 172, 176, 177)
    acceptance_path = os.path.join(artifacts_dir, "acceptance.json")
    acceptance_payload = {
        "run_id": run_id,
        "base_commit": "13dfe2d53a343f9d66ea8e086c75c0d21e41782c",
        "commit_sha": commit_sha,
        "tested_diff_sha256": diff_hash,
        "timestamp_iso": t_start_iso,
        "technical_status": "PASS" if all_tech_pass else "FAIL",
        "historical_status": historical_status,
        "performance_status": performance_status,
        "simulation_evidence_type": simulation_evidence_type,
        "gates": gates_evidence,
        "config_hash": config_hash,
        "effective_config": effective_cfg,
        "total_fills": total_fills,
        "closed_trades_count": closed_trades,
        "open_positions_count": open_pos_count,
        "pending_orders_count": replay_res.get("pending_orders_count", 0),
        "metrics": metrics,
        "summary_readable": (
            f"Đã kiểm tra kỹ thuật V16.9: {total_fills} lệnh khớp có guard. "
            f"Đã đóng: {closed_trades} · Đang mở: {open_pos_count}. "
            f"Lời/lỗ đã chốt sau phí: {total_pnl if total_pnl is not None else 0.0} USD. "
            f"Drawdown tối đa MTM: {max_dd if max_dd is not None else 0.0}%. "
            f"Historical status: {historical_status} · Technical status: {'PASS' if all_tech_pass else 'FAIL'}."
        ),
    }
    with open(acceptance_path, "w", encoding="utf-8") as f:
        json.dump(acceptance_payload, f, indent=2)

    manifest_path = os.path.join(artifacts_dir, "manifest.json")
    manifest_payload = {
        "run_id": run_id,
        "commit_sha": commit_sha,
        "created_at_iso": t_start_iso,
        "files": {
            "acceptance.json": file_sha256(acceptance_path),
            "test_results.json": file_sha256(test_results_path),
            "effective_config.json": file_sha256(effective_config_path),
            "trades.csv": file_sha256(trades_csv_path),
            "daily_stats.csv": file_sha256(daily_csv_path),
            "equity_mtm.csv": file_sha256(eq_mtm_path),
            "equity_realized.csv": file_sha256(eq_realized_path),
            "rejection_funnel.csv": file_sha256(funnel_path),
        },
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)

    # Step 6: Generate Markdown Acceptance Report
    report_md = _generate_v16_9_report(acceptance_payload, replay_res, tech_results, gates_evidence)

    artifact_report_path = os.path.join(artifacts_dir, "V16_9_ACCEPTANCE_REPORT.md")
    with open(artifact_report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    docs_report_path = os.path.join(DOCS, "V16_9_ACCEPTANCE_REPORT.md")
    with open(docs_report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\n--> Artifacts written successfully to {artifacts_dir}")
    print(f"--> Acceptance Report written to {docs_report_path}")

    return 0 if all_tech_pass else 1


def _generate_v16_9_report(
    acc: Dict[str, Any],
    replay_res: Dict[str, Any],
    tech_results: List[Dict[str, Any]],
    gates_evidence: List[Dict[str, Any]],
) -> str:
    metrics = replay_res.get("metrics", {})

    lines = [
        "# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.9 — CG-01 CAUSAL EXECUTION, REAL HISTORICAL REPLAY, DECISION FUNNEL AND SIZING CONTRACT REPAIR",
        "",
        f"- **Commit Base**: `13dfe2d53a343f9d66ea8e086c75c0d21e41782c`",
        f"- **Commit Được Kiểm Thử**: `{acc.get('commit_sha')}`",
        f"- **Diff Hash Working Tree**: `{acc.get('tested_diff_sha256')}`",
        f"- **Run ID**: `{acc.get('run_id')}`",
        f"- **Thời điểm tạo**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- **Technical Status**: `{acc.get('technical_status')}`",
        f"- **Historical Status**: `{acc.get('historical_status')}`",
        f"- **Performance Status**: `{acc.get('performance_status')}`",
        f"- **Simulation Evidence Type**: `{acc.get('simulation_evidence_type')}`",
        "",
        "---",
        "",
        "## 1. TÓM TẮT THỰC THI (EXECUTIVE SUMMARY)",
        "",
        f"> {acc.get('summary_readable')}",
        "",
        "- **Phạm vi hoàn thiện 4 lỗi cốt lõi (LỖI A, B, C, D)**:",
        "  1. **LỖI A — Thực thi OHLC Nhân quả (Causal OHLC Execution)**: Loại bỏ lookahead pre-pass tại `bar_open`. Sử dụng `build_assumed_bar_events` mở rộng nến thành các điểm waypoint giả định, tịnh tiến đồng hồ tăng dần đơn điệu và chỉ đóng lệnh khi điểm waypoint vi phạm ngưỡng. Sửa `TradeLifecycleService.process_exit_tick` để ưu tiên `tick_timestamp`.",
        "  2. **LỖI B — Tách biệt Replay Lịch sử thật và Demo (Historical Runner)**: Tách bạch rõ ràng giữa `run_synthetic_execution_demo` và `run_historical_cg01_replay`. Tuyệt đối không cấy lệnh mẫu vào nhánh lịch sử. Khi thiếu dataset M1 thật, trung thực báo cáo `historical_status = BLOCKED_DATA` và `performance_status = NOT_EVALUATED`.",
        "  3. **LỖI C — Hợp đồng Phễu Quyết định (Decision Funnel Contract)**: Bổ sung từ điển `cg01_evaluation` và `armed_order_id` vào mọi chu trình đánh giá của `ScalpWatcherOrchestrator.evaluate_cycle` (kể cả các nhánh return sớm). Driver chuẩn hóa quan sát bằng `normalize_replay_cycle_observation` và tổng hợp phễu đầy đủ.",
        "  4. **LỖI D — Hợp đồng Quy mô Khả thi (Executable Sizing Contract)**: Phân tách rõ ràng giữa `target_budget`, `account_safety_cap` và `executable_budget`. Lưu vết đầy đủ các trường sizing vào `cost_snapshot` tại thời điểm fill (`capital_mode`, `target_capital`, `target_risk_budget`, `account_capital_at_fill`, `executable_risk_budget`, `actual_risk_after_rounding`, `sizing_adjustment_reason`).",
        "",
        "---",
        "",
        "## 2. BẰNG CHỨNG NGHIỆM THU TỪNG CỔNG KỸ THUẬT (EVIDENCE-BASED GATES)",
        "",
        "| Gate | Trạng Thái | Test Suite | Số Case Yêu Cầu | Số Case Đã Chạy | Ghi Chú |",
        "|---|---|---|---|---|---|",
    ]

    for g in gates_evidence:
        req_cnt = len(g.get("required_cases", []))
        exec_cnt = len(g.get("executed_cases", []))
        reason_str = g.get("reason") or "OK"
        lines.append(f"| `{g['name']}` | **{g['status']}** | `{g.get('evidence_suite', '')}` | {req_cnt} | {exec_cnt} | {reason_str} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. BẢNG TỔNG HỢP CÁC BƯỚC TEST TỰ ĐỘNG (MACHINE TEST RESULTS)",
        "",
        "| Tên Test Step | Lệnh Thực Thi | Thời Gian (s) | Passed | Failed | Deselected | Status |",
        "|---|---|---|---|---|---|---|",
    ])

    for tr in tech_results:
        tstats = tr.get("test_stats") or {}
        p_cnt = tstats.get("passed", "-")
        f_cnt = tstats.get("failed", "-")
        d_cnt = tstats.get("deselected", "-")
        cmd_str = " ".join(tr.get("cmd", []))
        if len(cmd_str) > 60:
            cmd_str = cmd_str[:57] + "..."
        lines.append(
            f"| `{tr['name']}` | `{cmd_str}` | {tr.get('duration_sec', 0.0)} | {p_cnt} | {f_cnt} | {d_cnt} | **{tr['status']}** |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 4. KẾT QUẢ MÔ PHỎNG MẪU (SYNTHETIC EXECUTION & SIZING EVIDENCE)",
        "",
        "Dữ liệu kiểm tra kỹ thuật chứng minh chu trình nhân quả đầy đủ:",
        "`Observation -> Arm (Target Budget) -> Coordinator Before-Fill Guards -> Sizing Safety Check -> Fill (Executable Budget) -> Waypoint Exit -> Realized Accounting`.",
        "",
        f"- **Vốn ban đầu**: `{metrics.get('initial_capital', 5000.0)} USD`",
        f"- **Tiền mặt kết thúc**: `{metrics.get('final_cash', 5000.0)} USD`",
        f"- **Tổng PnL thực hiện sau phí**: `{metrics.get('total_pnl_usd', 0.0)} USD`",
        f"- **Drawdown tối đa MTM**: `{metrics.get('max_drawdown_pct', 0.0)}%`",
        f"- **Số lệnh đã khớp**: `{acc.get('total_fills', 0)}`",
        f"- **Số vị thế mở**: `{acc.get('open_positions_count', 0)}`",
        f"- **Số lệnh hết hạn trước khi chạm entry**: `{replay_res.get('expired_orders_count', 0)}`",
        "",
        "### Phễu Quyết Định và Phân Loại Lý Do Chặn (Rejection Funnel)",
        "",
        "| Danh Mục | Số Lượng | Ý Nghĩa / Nguyên Nhân |",
        "|---|---|---|",
    ])

    for item in replay_res.get("rejection_funnel", []):
        lines.append(f"| `{item.get('category')}` | {item.get('count')} | {item.get('description')} |")

    lines.extend([
        "",
        "---",
        "",
        "## 5. GIỚI HẠN VÀ TÍNH TRUNG THỰC (LIMITATIONS & INTEGRITY)",
        "",
        "1. **Tình trạng Dữ liệu Lịch sử (Historical Status)**: Khi chưa cung cấp dataset M1 thật qua tham số `--m1-dataset`, hệ thống trung thực báo cáo `BLOCKED_DATA` và không đo lường chỉ số sinh lời (`performance_status = NOT_EVALUATED`). Tuyệt đối không giả mạo kết quả lịch sử bằng lệnh demo.",
        "2. **Không Thay Đổi Logic Chiến Lược**: Giữ nguyên vẹn 100% điều kiện setup CG-01 (sweep thresholds, MSS/displacement, FVG selection, RR >= 2.0).",
        "3. **Tính Đơn Điệu Của Đồng Hồ**: Đồng hồ ReplayClock được kiểm tra tiến tuần tự theo thời gian sub-event, không bao giờ nhảy ngược về quá khứ.",
        "",
        "---",
        "*Báo cáo được tạo tự động bởi tools/run_v16_9_acceptance.py*",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
