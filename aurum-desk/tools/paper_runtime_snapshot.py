"""
tools/paper_runtime_snapshot.py
V16.39.10 — Forward Paper Runtime Observation & Read-Only Snapshot Tool

Strictly Read-Only (Rules 001-145):
- Never mutates trading configurations or user auto authority.
- Never creates spurious runs, policy rows, or checkpoints.
- Never sends live Bitget trading orders or modifies account balances.
- Does not start background workers, loops, or schedulers.
- Supports both HTTP live backend query (via canonical /api/v1/paper/runtime-snapshot)
  and offline SQLite read-only mode (using mode=ro and PRAGMA query_only=ON).
- Truthful financial accounting: expectancy 0.00 on breakeven, N/A on zero closed trades.
- True 3-state Auto authority (True / False / None) and truthful worker health reporting.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

# Ensure backend directory is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from services.paper_runtime_snapshot_service import (
    SCHEMA_VERSION,
    aggregate_funnel_readonly,
    build_paper_runtime_snapshot,
    compute_financial_metrics,
    find_existing_observation_run,
    get_git_commit,
    make_snapshot_context,
    read_auto_authority,
    read_existing_policy_config,
    read_linked_orders,
    read_run_funnel_records,
    redact_sensitive_text,
    render_markdown as service_render_markdown,
    validate_finite_metrics,
)

logger = logging.getLogger("paper_runtime_snapshot")


def resolve_snapshot_db_path(
    explicit_path: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Optional[Path]:
    """
    Rules 018, 116, 117, 118: Resolves the database path in strict priority order:
    1. --db command line argument.
    2. AURUM_DB_PATH environment variable.
    3. backend/aurum_desk.db (standard repo database).
    Never searches for non-existent backend/data/trading.db.
    If the file does not exist, returns None without creating files or directories.
    """
    env = environ if environ is not None else os.environ

    if explicit_path:
        target = Path(explicit_path)
        if not target.is_absolute():
            target = (PROJECT_ROOT / target).resolve()
        return target if target.is_file() else None

    env_path = env.get("AURUM_DB_PATH")
    if env_path:
        target = Path(env_path)
        if not target.is_absolute():
            target = (PROJECT_ROOT / target).resolve()
        return target if target.is_file() else None

    default_path = (BACKEND_DIR / "aurum_desk.db").resolve()
    return default_path if default_path.is_file() else None


@contextlib.contextmanager
def open_snapshot_session(db_path: Path) -> Generator[Session, None, None]:
    """
    Rules 119-121: Opens a guaranteed read-only SQLite session using SQLite URI mode=ro
    and sets PRAGMA query_only=ON. Verifies connection with SELECT 1.
    Disposes engine and closes session in finally.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"DATABASE_NOT_FOUND: {db_path}")

    # SQLite read-only URI
    db_uri = f"sqlite:///file:{db_path.resolve().as_posix()}?mode=ro&uri=true"
    engine = create_engine(
        db_uri,
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()
    try:
        # Enforce query_only pragma and verify read capability
        session.execute(text("PRAGMA query_only = ON;"))
        session.execute(text("SELECT 1;"))
        yield session
    finally:
        session.close()
        engine.dispose()


def parse_http_snapshot(payload: Any) -> Dict[str, Any]:
    """
    Rules 044, 108: Validates schema and integrity of HTTP snapshot response.
    Rejects invalid HTML, arrays, corrupted schemas, or payloads missing required fields.
    """
    if not isinstance(payload, dict):
        raise ValueError("Invalid snapshot payload: expected JSON object")

    schema_ver = payload.get("schema_version")
    if schema_ver != "2.0":
        raise ValueError(f"Unsupported schema_version: {schema_ver}. Expected '2.0'")

    required_top_keys = ("status", "metrics", "time_window", "effective_config", "worker_health")
    for k in required_top_keys:
        if k not in payload:
            raise ValueError(f"Invalid snapshot payload: missing required field '{k}'")

    if not isinstance(payload["metrics"], dict):
        raise ValueError("Invalid snapshot payload: missing or malformed metrics")

    required_metric_keys = ("duration_minutes", "closed_trades_count", "orders_count", "open_positions_count")
    for mk in required_metric_keys:
        if mk not in payload["metrics"]:
            raise ValueError(f"Invalid snapshot payload: missing required metric '{mk}'")

    validate_finite_metrics(payload["metrics"])
    return payload


def load_runtime_sources(
    url: Optional[str] = None,
    db_path: Optional[str] = None,
    run_id: Optional[str] = None,
    symbol: str = "XAUUSDT",
    scope: str = "CG01",
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rules 106-112: In HTTP mode, calls the canonical GET /api/v1/paper/runtime-snapshot.
    In offline mode, resolves DB path and verifies connectivity.
    """
    if url:
        base_url = url.rstrip("/")
        query_params = {
            "symbol": symbol,
            "scope": scope,
        }
        if run_id:
            query_params["run_id"] = run_id
        if start_ms is not None:
            query_params["start_ms"] = str(start_ms)
        if end_ms is not None:
            query_params["end_ms"] = str(end_ms)

        encoded_qs = urllib.parse.urlencode(query_params)
        canonical_ep = f"{base_url}/api/v1/paper/runtime-snapshot?{encoded_qs}"

        sources: Dict[str, Any] = {
            "mode": "HTTP",
            "url": canonical_ep,
            "connected": False,
            "status": "RUNTIME_NOT_AVAILABLE",
            "data": {},
        }
        try:
            req = urllib.request.Request(
                canonical_ep,
                headers={"User-Agent": "AurumDesk-Snapshot/2.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    raw_body = resp.read().decode("utf-8")
                    parsed = json.loads(raw_body)
                    validated = parse_http_snapshot(parsed)
                    sources["connected"] = True
                    sources["status"] = validated.get("status", "SNAPSHOT_READY")
                    sources["snapshot"] = validated
                    sources["data"]["canonical"] = validated
                else:
                    sources["status"] = "RUNTIME_NOT_AVAILABLE"
                    sources["detail"] = f"HTTP {resp.status}"
        except urllib.error.HTTPError as he:
            sources["status"] = "RUNTIME_NOT_AVAILABLE"
            if he.code == 404:
                sources["detail"] = "ENDPOINT_NOT_SUPPORTED: Backend does not expose /api/v1/paper/runtime-snapshot."
            else:
                sources["detail"] = f"HTTP_ERROR_{he.code}"
        except Exception as e:
            sources["status"] = "RUNTIME_NOT_AVAILABLE"
            sources["detail"] = redact_sensitive_text(str(e))

        return sources

    # Offline / database mode
    resolved_path = resolve_snapshot_db_path(explicit_path=db_path)
    if not resolved_path or not resolved_path.is_file():
        candidate_repr = db_path or os.getenv("AURUM_DB_PATH") or str(BACKEND_DIR / "aurum_desk.db")
        return {
            "mode": "OFFLINE_DB",
            "status": "DATABASE_NOT_FOUND",
            "detail": f"Database file not found: {candidate_repr}",
            "db_path": None,
        }

    return {
        "mode": "OFFLINE_DB",
        "status": "AVAILABLE",
        "db_path": str(resolved_path),
    }


def collect_existing_run_summary(
    sources: Dict[str, Any],
    run_id: Optional[str] = None,
    create_if_missing: bool = False,
) -> Dict[str, Any]:
    """
    Backwards-compatible adapter for tests and legacy callers.
    Never creates runs or mutates state.
    """
    mode = sources.get("mode")
    if mode == "HTTP" and sources.get("snapshot"):
        return sources["snapshot"].get("run_summary", {})

    if mode == "OFFLINE_DB" and sources.get("session"):
        session = sources["session"]
        run_record, resolution_status = find_existing_observation_run(
            session, run_id=run_id, symbol="XAUUSDT", scope="CG01"
        )
        if not run_record:
            return {
                "status": "NOT_FOUND",
                "resolution_status": resolution_status,
                "run_id": run_id or "NONE_ACTIVE",
                "total_closed_trades": 0,
                "net_realized_pnl_usd": 0.0,
                "winrate_pct": None,
                "detail": "No active or specified PaperResearchRun record found in database.",
            }

        orders, _ = read_linked_orders(session, run_id=run_record.run_id, symbol="XAUUSDT")
        fin = compute_financial_metrics(orders, initial_capital=run_record.initial_capital_usdt)

        return {
            "status": run_record.status,
            "resolution_status": resolution_status,
            "run_id": run_record.run_id,
            "execution_mode": run_record.execution_mode,
            "config_fingerprint": run_record.config_fingerprint,
            "total_closed_trades": fin["closed_trades_count"],
            "net_realized_pnl_usd": fin["net_realized_pnl_usd"] or 0.0,
            "winrate_pct": fin["winrate_pct"],
            "expectancy_usd_per_trade": fin["expectancy_usd_per_trade"],
            "trades": {
                "total_trades_count": fin["orders_count"],
                "total_closed_trades": fin["closed_trades_count"],
                "open_trades_count": fin["open_positions_count"],
                "armed_orders_count": fin["armed_orders_count"],
                "winrate_pct": fin["winrate_pct"],
            },
            "financial_metrics_post_fee": {
                "net_realized_pnl_usd": fin["net_realized_pnl_usd"] or 0.0,
                "max_drawdown_usd": fin["max_drawdown_usd"],
                "max_drawdown_pct": fin["max_drawdown_pct"],
                "drawdown_type": fin["drawdown_type"],
            },
        }

    return {
        "status": "NOT_FOUND",
        "resolution_status": "NOT_FOUND",
        "run_id": run_id or "NONE",
        "total_closed_trades": 0,
        "net_realized_pnl_usd": 0.0,
        "winrate_pct": None,
    }


def collect_funnel_summary(
    sources: Dict[str, Any],
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Backwards-compatible adapter for tests and legacy callers.
    """
    mode = sources.get("mode")
    if mode == "HTTP" and sources.get("snapshot"):
        return sources["snapshot"].get("funnel_summary", {})

    if mode == "OFFLINE_DB" and sources.get("session"):
        session = sources["session"]
        records = read_run_funnel_records(
            session, run_id=None, symbol="XAUUSDT", start_ms=start_ms, end_ms=end_ms
        )
        return aggregate_funnel_readonly(records)

    return aggregate_funnel_readonly([])


def build_runtime_snapshot(
    sources: Dict[str, Any],
    run_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Backwards-compatible adapter for tests and legacy callers.
    Directs to canonical build_paper_runtime_snapshot.
    """
    mode = sources.get("mode", "UNKNOWN")
    if mode == "HTTP":
        if "snapshot" in sources:
            return sources["snapshot"]

        if sources.get("status") == "CONNECTED":
            h_data = sources.get("data", {}).get("health", {})
            worker_status = h_data.get("status", "UNKNOWN")
            worker_running = h_data.get("running")
            is_stopped = worker_status == "STOPPED" or worker_running is False

            limitations = [
                "UNSUPPORTED_LEGACY_SOURCE: Cấu trúc test giả lập không còn được hỗ trợ; yêu cầu snapshot HTTP chuẩn."
            ]
            if is_stopped:
                limitations.append("WORKER_STOPPED: Evaluation worker is currently STOPPED.")

            return {
                "schema_version": SCHEMA_VERSION,
                "status": "STOPPED" if is_stopped else "NOT_CONFIGURED",
                "source_mode": "HTTP",
                "error": "UNSUPPORTED_LEGACY_SOURCE",
                "detail": "Connected HTTP source did not yield canonical validated snapshot payload.",
                "worker_health": {
                    "status": worker_status,
                    "running": worker_running,
                    "source": "HTTP_HEALTH_ENDPOINT",
                },
                "metrics": {
                    "closed_trades_count": 0,
                    "winrate_pct": None,
                    "expectancy_usd_per_trade": None,
                    "net_realized_pnl_usd": None,
                    "orders_count": 0,
                    "duration_minutes": 0.0,
                    "opportunities_count": 0,
                    "open_positions_count": 0,
                    "armed_orders_count": 0,
                    "max_drawdown_usd": 0.0,
                    "max_drawdown_pct": None,
                    "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",
                },
                "run_summary": {"status": "NONE", "run_id": run_id or "NONE"},
                "funnel_summary": aggregate_funnel_readonly([]),
                "effective_config": {"auto_enabled": None},
                "limitations": limitations,
            }

        # Backend unreachable
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "RUNTIME_NOT_AVAILABLE",
            "source_mode": "HTTP",
            "worker_health": {"status": "UNKNOWN", "running": None},
            "metrics": {
                "closed_trades_count": 0,
                "winrate_pct": None,
                "expectancy_usd_per_trade": None,
                "net_realized_pnl_usd": None,
                "orders_count": 0,
                "duration_minutes": 0.0,
                "opportunities_count": 0,
                "open_positions_count": 0,
                "armed_orders_count": 0,
                "max_drawdown_usd": 0.0,
                "max_drawdown_pct": None,
                "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",
            },
            "run_summary": {"status": "UNAVAILABLE", "run_id": run_id or "NONE"},
            "funnel_summary": aggregate_funnel_readonly([]),
            "effective_config": {"auto_enabled": None},
            "limitations": [
                "RUNTIME_NOT_AVAILABLE: No active backend connection or readable database file."
            ],
        }

    # Offline session mode
    session = sources.get("session")
    return build_paper_runtime_snapshot(
        session,
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        start_ms=start_ms,
        end_ms=end_ms,
        source_mode="OFFLINE_DB",
    )


def render_markdown(snapshot: Dict[str, Any]) -> str:
    """Renders human-friendly markdown report."""
    return service_render_markdown(snapshot)


def main() -> int:
    """
    Main CLI entrypoint adhering to Rules 129-135.
    Exit codes:
      0: Successful complete snapshot.
      1: Parameter error, database not found, or fatal error.
      2: Partial snapshot or runtime not available.
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Aurum Desk V16.39.10 Forward Paper Runtime Observation & Read-Only Snapshot Tool"
    )
    src_group = parser.add_mutually_exclusive_group()
    src_group.add_argument(
        "--url",
        type=str,
        default=None,
        help="HTTP backend base URL (e.g. http://127.0.0.1:8000)",
    )
    src_group.add_argument(
        "--db",
        type=str,
        default=None,
        help="Explicit path to SQLite database file for offline read-only snapshot",
    )

    parser.add_argument("--run-id", type=str, default=None, help="Specific observation run ID")
    parser.add_argument("--symbol", type=str, default="XAUUSDT", help="Trading instrument (default: XAUUSDT)")
    parser.add_argument("--scope", type=str, default="CG01", help="Strategy scope (default: CG01)")
    parser.add_argument("--start-ms", type=int, default=None, help="Start epoch timestamp in ms (inclusive)")
    parser.add_argument("--end-ms", type=int, default=None, help="End epoch timestamp in ms (exclusive)")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save snapshot JSON and Markdown files")
    parser.add_argument(
        "--format",
        type=str,
        choices=["json", "markdown", "both"],
        default="both",
        help="Output format (default: both)",
    )

    args = parser.parse_args()

    # Validate window parameters early (Rule 025)
    if args.start_ms is not None and args.end_ms is not None and args.end_ms <= args.start_ms:
        sys.stderr.write(f"Lỗi tham số: end-ms ({args.end_ms}) phải lớn hơn start-ms ({args.start_ms})\n")
        return 1

    snapshot: Dict[str, Any] = {}

    if args.url:
        sources = load_runtime_sources(
            url=args.url,
            run_id=args.run_id,
            symbol=args.symbol,
            scope=args.scope,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
        if sources.get("snapshot"):
            snapshot = sources["snapshot"]
        else:
            snapshot = build_runtime_snapshot(sources, run_id=args.run_id, start_ms=args.start_ms, end_ms=args.end_ms)
    else:
        # Offline mode
        db_path = resolve_snapshot_db_path(explicit_path=args.db)
        if db_path is None or not db_path.is_file():
            target_repr = args.db or os.getenv("AURUM_DB_PATH") or str(BACKEND_DIR / "aurum_desk.db")
            sys.stderr.write(f"Lỗi: Không tìm thấy tệp cơ sở dữ liệu: {target_repr}\n")
            return 1

        try:
            with open_snapshot_session(db_path) as session:
                snapshot = build_paper_runtime_snapshot(
                    session,
                    run_id=args.run_id,
                    symbol=args.symbol,
                    scope=args.scope,
                    start_ms=args.start_ms,
                    end_ms=args.end_ms,
                    source_mode="OFFLINE_DB",
                )
        except Exception as e:
            sys.stderr.write(f"Lỗi khi đọc snapshot từ DB: {redact_sensitive_text(str(e))}\n")
            return 1

    md_output = render_markdown(snapshot)
    json_output = json.dumps(snapshot, indent=2, ensure_ascii=False)

    # Safe atomic file writing if output-dir is specified (Rule 132, 133)
    if args.output_dir:
        out_path = Path(args.output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        if args.format in ("json", "both"):
            tmp_json = out_path / ".paper_runtime_snapshot.json.tmp"
            tmp_json.write_text(json_output, encoding="utf-8")
            tmp_json.replace(out_path / "paper_runtime_snapshot.json")
        if args.format in ("markdown", "both"):
            tmp_md = out_path / ".paper_runtime_snapshot.md.tmp"
            tmp_md.write_text(md_output, encoding="utf-8")
            tmp_md.replace(out_path / "paper_runtime_snapshot.md")
        print(f"Snapshot written to {out_path.resolve()}/paper_runtime_snapshot.[json|md]")
    else:
        if args.format == "json":
            print(json_output)
        elif args.format == "markdown":
            print(md_output)
        else:
            print("=== PAPER RUNTIME SNAPSHOT (JSON) ===")
            print(json_output)
            print("\n=== PAPER RUNTIME SNAPSHOT (MARKDOWN) ===")
            print(md_output)

    # Exit code determination
    status = snapshot.get("status")
    if status in ("SNAPSHOT_READY", "RUNNING", "STOPPED", "NOT_CONFIGURED"):
        return 0
    elif status == "PARTIAL":
        return 2
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
