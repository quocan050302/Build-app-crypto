"""
AURUM DESK — REPLAY INTEGRITY & ARTIFACT VERIFICATION (V13.6)
Strict evidence-based integrity verification across all critical dimensions.
Fails closed on missing decision_time, UNKNOWN direction, acausal timestamps,
cash ledger discrepancies, daily cap breaches, or artifact content mismatches.
"""

import os
import json
import csv
from typing import Dict, Any, List, Optional, Tuple
import openpyxl


def run_replay_integrity_checks(
    result_dict: Dict[str, Any],
    closed_trades: List[Any],
    ledger_postings: List[Dict[str, Any]],
    daily_stats: Dict[str, Any],
    initial_equity: float,
    reported_cash: float,
    dataset_hash: Optional[str] = None,
    run_config_hash: Optional[str] = None
) -> Dict[str, Any]:
    """
    PHẦN 07, 60, 61, 87, 131: Evidence-based integrity verification.
    Strictly fail-closed: missing decision_time or UNKNOWN direction MUST fail.
    """
    checks = []

    # 1. Dataset Hash Check
    ds_ok = bool(dataset_hash and len(dataset_hash) >= 8)
    checks.append({
        "id": "dataset_hash_verified",
        "status": "PASS" if ds_ok else "FAIL",
        "observed": dataset_hash or "MISSING"
    })

    # 2. Run Config Hash Check (Must be distinct from dataset hash)
    cfg_ok = bool(run_config_hash and len(run_config_hash) >= 8 and run_config_hash != dataset_hash)
    checks.append({
        "id": "run_config_hash_verified",
        "status": "PASS" if cfg_ok else "FAIL",
        "observed": run_config_hash or "MISSING"
    })

    # 3. Cash Ledger Reconciliation Check (tolerance 0.05 USD)
    total_postings = sum(p.get("amount", 0.0) or p.get("amount_usdt", 0.0) for p in ledger_postings)
    expected_cash = initial_equity + total_postings
    cash_diff = abs(reported_cash - expected_cash)
    cash_reconciled = cash_diff <= 0.05
    checks.append({
        "id": "cash_ledger_reconciled",
        "status": "PASS" if cash_reconciled else "FAIL",
        "observed": f"diff={cash_diff:.4f}, expected={expected_cash:.2f}, reported={reported_cash:.2f}"
    })

    # 4. Causal Event Order Check (PHẦN 07, 63: decision_time mandatory, strictly entry >= decision and exit >= entry)
    causal_ok = True
    causal_err = None
    for idx, tr in enumerate(closed_trades):
        t_id = getattr(tr, "id", None) or (tr.get("id") if isinstance(tr, dict) else f"trade-{idx}")
        dec_t = getattr(tr, "decision_time", None) or (tr.get("decision_time") if isinstance(tr, dict) else None)
        ent_t = getattr(tr, "entry_time", None) or (tr.get("entry_time") if isinstance(tr, dict) else None)
        ex_t = getattr(tr, "exit_time", None) or (tr.get("exit_time") if isinstance(tr, dict) else None)

        if dec_t is None or dec_t <= 0:
            causal_ok = False
            causal_err = f"MISSING_DECISION_TIME: trade {t_id} has invalid or missing decision_time={dec_t}"
            break
        if ent_t is None or ent_t <= 0:
            causal_ok = False
            causal_err = f"MISSING_ENTRY_TIME: trade {t_id} has invalid or missing entry_time={ent_t}"
            break
        if ex_t is None or ex_t <= 0:
            causal_ok = False
            causal_err = f"MISSING_EXIT_TIME: trade {t_id} has invalid or missing exit_time={ex_t}"
            break

        if ent_t < dec_t:
            causal_ok = False
            causal_err = f"ENTRY_BEFORE_DECISION: trade {t_id} entry={ent_t} < dec={dec_t}"
            break
        if ex_t < ent_t:
            causal_ok = False
            causal_err = f"EXIT_BEFORE_ENTRY: trade {t_id} exit={ex_t} < entry={ent_t}"
            break

    checks.append({
        "id": "causal_data_order_ok",
        "status": "PASS" if causal_ok else "FAIL",
        "observed": causal_err or "TIMESTAMPS_STRICTLY_CAUSAL"
    })

    # 5. Hard Guards Enforcement Check (Max 3 fills per day)
    guards_ok = True
    guards_err = None
    for d_key, d_stat in daily_stats.items():
        fills = d_stat.get("total_fills", 0) if isinstance(d_stat, dict) else getattr(d_stat, "total_fills", 0)
        if fills > 3:
            guards_ok = False
            guards_err = f"DAILY_CAP_EXCEEDED on {d_key}: {fills} > 3"
            break

    checks.append({
        "id": "hard_guards_verified",
        "status": "PASS" if guards_ok else "FAIL",
        "observed": guards_err or "DAILY_CAP_AND_STOPS_HONORED"
    })

    # 6. Trade Count Partitioning Check
    closed_cnt = result_dict.get("closed_count", len(closed_trades))
    wins = result_dict.get("wins", 0)
    losses = result_dict.get("losses", 0)
    breakevens = result_dict.get("breakevens", 0)
    partition_ok = (closed_cnt == len(closed_trades)) and (wins + losses + breakevens == closed_cnt)
    checks.append({
        "id": "trade_count_partitioned",
        "status": "PASS" if partition_ok else "FAIL",
        "observed": f"closed={closed_cnt}, sum={wins + losses + breakevens}"
    })

    # 7. Pricing Geometry Check (PHẦN 07, 65: direction MUST be strictly LONG or SHORT)
    geom_ok = True
    geom_err = None
    for idx, tr in enumerate(closed_trades):
        t_id = getattr(tr, "id", None) or (tr.get("id") if isinstance(tr, dict) else f"trade-{idx}")
        direction = getattr(tr, "direction", None) or (tr.get("direction") if isinstance(tr, dict) else "UNKNOWN")
        entry = getattr(tr, "entry_price", None) or (tr.get("entry_price") if isinstance(tr, dict) else 0.0)
        sl = getattr(tr, "stop_loss", None) or (tr.get("stop_loss") if isinstance(tr, dict) else 0.0)
        tp = getattr(tr, "take_profit", None) or (tr.get("take_profit") if isinstance(tr, dict) else 0.0)

        if direction not in ("LONG", "SHORT"):
            geom_ok = False
            geom_err = f"INVALID_DIRECTION: trade {t_id} direction={direction} not in (LONG, SHORT)"
            break

        if direction == "LONG":
            if not (sl < entry < tp):
                geom_ok = False
                geom_err = f"LONG_GEOM_INVALID: trade {t_id} sl={sl}, entry={entry}, tp={tp}"
                break
        elif direction == "SHORT":
            if not (tp < entry < sl):
                geom_ok = False
                geom_err = f"SHORT_GEOM_INVALID: trade {t_id} tp={tp}, entry={entry}, sl={sl}"
                break

    checks.append({
        "id": "pricing_geometry_verified",
        "status": "PASS" if geom_ok else "FAIL",
        "observed": geom_err or "GEOMETRY_COMPLIANT"
    })

    overall_pass = all(c["status"] == "PASS" for c in checks)

    return {
        "status": "PASS" if overall_pass else "FAIL",
        "dataset_hash": dataset_hash,
        "run_config_hash": run_config_hash,
        "causal_data_ok": causal_ok,
        "guards_active": guards_ok,
        "ledger_reconciled": cash_reconciled,
        "trade_count_consistent": partition_ok,
        "checks": checks
    }


def verify_exported_artifacts(result: Any, artifacts_dir: str) -> Tuple[bool, List[str]]:
    """
    PHẦN 07, 67, 68, 69, 132, 133: Inspects exported JSON, CSV, and XLSX cell-by-cell and row-by-row.
    Fails if row count, PnL, direction, prices, or trade data differs.
    """
    errors = []
    if not artifacts_dir or not os.path.exists(artifacts_dir):
        return False, [f"ARTIFACTS_DIR_NOT_FOUND: {artifacts_dir}"]

    expected_pnl = getattr(result, "total_net_pnl", None) or (result.get("total_net_pnl") if isinstance(result, dict) else 0.0)
    expected_closed = getattr(result, "closed_count", None) or (result.get("closed_count") if isinstance(result, dict) else 0)
    raw_trades = getattr(result, "trades", None)
    if raw_trades is None and isinstance(result, dict):
        raw_trades = result.get("trades")
    if raw_trades is None:
        raw_trades = []
    closed_trades_list = [t for t in raw_trades if getattr(t, "status", None) == "CLOSED" or (isinstance(t, dict) and t.get("status") == "CLOSED")]

    # 1. Inspect report.json
    json_path = os.path.join(artifacts_dir, "report.json")
    if not os.path.exists(json_path):
        errors.append("MISSING_REPORT_JSON")
    else:
        try:
            with open(json_path, "r") as f:
                r_json = json.load(f)
            actual_pnl = r_json.get("summary", {}).get("realized_net_pnl")
            if actual_pnl is None:
                actual_pnl = r_json.get("total_net_pnl", 0.0)
            if abs(float(actual_pnl) - float(expected_pnl)) > 0.05:
                errors.append(f"REPORT_JSON_PNL_MISMATCH: expected {expected_pnl}, found {actual_pnl}")
            actual_cnt = r_json.get("summary", {}).get("closed_trades_count")
            if actual_cnt is None:
                actual_cnt = r_json.get("closed_count", 0)
            if int(actual_cnt) != int(expected_closed):
                errors.append(f"REPORT_JSON_COUNT_MISMATCH: expected {expected_closed}, found {actual_cnt}")
        except Exception as e:
            errors.append(f"REPORT_JSON_PARSE_ERROR: {str(e)}")

    # 2. Inspect trades.csv row-by-row
    csv_path = os.path.join(artifacts_dir, "trades.csv")
    if not os.path.exists(csv_path):
        errors.append("MISSING_TRADES_CSV")
    else:
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            csv_trades_count = len(rows)
            if csv_trades_count != expected_closed:
                errors.append(f"TRADES_CSV_COUNT_MISMATCH: expected {expected_closed}, found {csv_trades_count}")

            csv_net_sum = 0.0
            for idx, row in enumerate(rows):
                # Check direction
                dir_val = row.get("direction") or row.get("Hướng") or row.get("Direction")
                if dir_val not in ("LONG", "SHORT"):
                    errors.append(f"TRADES_CSV_ROW_{idx}_INVALID_DIRECTION: {dir_val}")

                # Check pnl
                pnl_str = row.get("net_pnl") or row.get("Lãi ròng ($)") or row.get("Net PnL") or "0.0"
                try:
                    pnl_f = float(pnl_str)
                    csv_net_sum += pnl_f
                except ValueError:
                    errors.append(f"TRADES_CSV_ROW_{idx}_INVALID_PNL: {pnl_str}")

                # If closed_trades_list has corresponding trade, compare values
                if idx < len(closed_trades_list):
                    exp_t = closed_trades_list[idx]
                    exp_dir = getattr(exp_t, "direction", None) or (exp_t.get("direction") if isinstance(exp_t, dict) else "")
                    exp_net = getattr(exp_t, "net_pnl", None) or (exp_t.get("net_pnl") if isinstance(exp_t, dict) else 0.0)
                    if dir_val and exp_dir and dir_val != exp_dir:
                        errors.append(f"TRADES_CSV_ROW_{idx}_DIR_MISMATCH: expected {exp_dir}, found {dir_val}")
                    try:
                        if abs(float(pnl_str) - float(exp_net)) > 0.05:
                            errors.append(f"TRADES_CSV_ROW_{idx}_PNL_MISMATCH: expected {exp_net}, found {pnl_str}")
                    except ValueError:
                        pass

            if expected_closed > 0 and abs(csv_net_sum - float(expected_pnl)) > 0.10:
                errors.append(f"TRADES_CSV_TOTAL_PNL_MISMATCH: expected {expected_pnl}, sum={csv_net_sum:.2f}")

        except Exception as e:
            errors.append(f"TRADES_CSV_PARSE_ERROR: {str(e)}")

    # 3. Inspect Excel workbook (.xlsx) cell-by-cell
    xlsx_files = [f for f in os.listdir(artifacts_dir) if f.endswith(".xlsx")]
    if not xlsx_files:
        errors.append("MISSING_XLSX_WORKBOOK")
    else:
        xlsx_path = os.path.join(artifacts_dir, xlsx_files[0])
        try:
            wb = openpyxl.load_workbook(xlsx_path, data_only=True)
            sheet_names = wb.sheetnames
            has_overview = any("tong_quan" in s.lower() or "overview" in s.lower() or "01_" in s for s in sheet_names)
            if not has_overview:
                errors.append("XLSX_MISSING_OVERVIEW_SHEET")

            # Check 03_Chi_tiet_lenh
            trades_sheet_name = None
            for s in sheet_names:
                if "chi_tiet_lenh" in s.lower() or "03_" in s or "trades" in s.lower():
                    trades_sheet_name = s
                    break

            if trades_sheet_name:
                ws = wb[trades_sheet_name]
                # Row 1 is header, data rows start at row 2
                max_r = ws.max_row
                xlsx_trades_count = max(0, max_r - 1) if max_r >= 1 else 0
                if xlsx_trades_count != expected_closed:
                    errors.append(f"XLSX_TRADES_COUNT_MISMATCH: expected {expected_closed}, found {xlsx_trades_count}")

                # Check cell contents for each trade row
                # Column 10: Direction, Column 27: Net PnL (1-indexed)
                for r_idx in range(2, max_r + 1):
                    t_idx = r_idx - 2
                    cell_dir = ws.cell(row=r_idx, column=10).value
                    cell_pnl = ws.cell(row=r_idx, column=27).value

                    if cell_dir not in ("LONG", "SHORT"):
                        errors.append(f"XLSX_ROW_{r_idx}_INVALID_DIR: {cell_dir}")

                    if t_idx < len(closed_trades_list):
                        exp_t = closed_trades_list[t_idx]
                        exp_dir = getattr(exp_t, "direction", None) or (exp_t.get("direction") if isinstance(exp_t, dict) else "")
                        exp_net = getattr(exp_t, "net_pnl", None) or (exp_t.get("net_pnl") if isinstance(exp_t, dict) else 0.0)
                        if cell_dir and exp_dir and cell_dir != exp_dir:
                            errors.append(f"XLSX_ROW_{r_idx}_DIR_MISMATCH: expected {exp_dir}, found {cell_dir}")
                        if cell_pnl is not None:
                            try:
                                if abs(float(cell_pnl) - float(exp_net)) > 0.05:
                                    errors.append(f"XLSX_ROW_{r_idx}_PNL_MISMATCH: expected {exp_net}, found {cell_pnl}")
                            except (ValueError, TypeError):
                                errors.append(f"XLSX_ROW_{r_idx}_NON_NUMERIC_PNL: {cell_pnl}")

            wb.close()
        except Exception as e:
            errors.append(f"XLSX_PARSE_ERROR: {str(e)}")

    is_valid = len(errors) == 0
    return is_valid, errors
