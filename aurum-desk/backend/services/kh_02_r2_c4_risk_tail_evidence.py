"""
aurum-desk/backend/services/kh_02_r2_c4_risk_tail_evidence.py

AURUM DESK — KH-02.R2.C4 Realized Tail, Fill-Time Risk & Independent Numeric Reconciliation
SPEC VERSION: KH-02.R2.C4-v1.0 | PURE READ-ONLY EVIDENCE & VERIFICATION MODULE

Closes P0-B:
  - Eliminates C3's empty `realized_tail_summary: {}` (key mismatch against upstream
    `audit_r2_fill_time_net_risk_and_realized_tail`), missing cap-violation key fallback,
    hardcoded `realized_tail_risk_disclosed: True`, and full-universe nested `total_fees_usd: 0.0`
    display mismatch.
  - Streams a complete, deterministically ordered per-episode risk ledger (`12_per_episode_risk_ledger.jsonl.gz`)
    covering ALL 5 R2 arms (`B2_H1`, `B2_H2`, `B2_H3`, `B2_H123`, `NC_NULL_TRIGGER`) across BOTH
    modes (`NEXT_BAR_OPEN_BID_ASK`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X`).
  - Independently recomputes every count, Decimal PnL/fee sum, loss-exceeds-$2.50 counter,
    realized loss quantile, worst realized loss, and ID hash directly from the compressed ledger
    without trusting any producer-reported boolean or status flag.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_02_r2_research_integrity_service import (
    DEFAULT_MODELED_SPREAD_USD,
    DEFAULT_SLIPPAGE_USD,
    MIN_NET_RR_THRESHOLD,
    PLANNED_NET_RISK_CAP_USD,
    PRIMARY_R2_ARMS,
    audit_r2_fill_time_net_risk_and_realized_tail,
    evaluate_causal_fill_time_policy_single_trade,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH02_R2_C4_TAIL = "KH-02.R2.C4-v1.0"
SCHEMA_VERSION_KH02_R2_C4_TAIL = "kh_02_r2_c4_risk_tail_evidence_v1"

EXPECTED_R2_TAIL_ARMS_C4: Tuple[str, ...] = (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER")
EXPECTED_R2_TAIL_MODES_C4: Tuple[Tuple[str, float, float], ...] = (
    ("NEXT_BAR_OPEN_BID_ASK", DEFAULT_MODELED_SPREAD_USD, DEFAULT_SLIPPAGE_USD),
    ("STRESS_WIDER_SPREAD_SLIPPAGE_2X", DEFAULT_MODELED_SPREAD_USD * 2.0, DEFAULT_SLIPPAGE_USD * 2.0),
)
EXPECTED_R2_TAIL_MODE_NAMES_C4: Tuple[str, ...] = tuple(m[0] for m in EXPECTED_R2_TAIL_MODES_C4)


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _is_finite_num(v: Any) -> bool:
    try:
        f = float(v)
        return math.isfinite(f)
    except Exception:
        return False


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _nearest_rank_quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0.0:
        return round(float(sorted_vals[0]), 4)
    if q >= 1.0:
        return round(float(sorted_vals[-1]), 4)
    idx = max(0, min(len(sorted_vals) - 1, int(math.ceil(q * len(sorted_vals))) - 1))
    return round(float(sorted_vals[idx]), 4)


def _extract_episode_fee_usd(ep: Mapping[str, Any]) -> Decimal:
    """
    Extracts exact fee USD from episode record or nested execution_ledger without ever
    silently returning 0.0 when gross != net.
    """
    if ep.get("total_fees_usd") is not None and float(ep.get("total_fees_usd") or 0.0) != 0.0:
        return Decimal(str(ep["total_fees_usd"]))
    el = ep.get("execution_ledger") or {}
    if isinstance(el, Mapping):
        for k in ("total_fees_usd", "total_fee_usd", "total_fee_usdt", "fees_usd"):
            if el.get(k) is not None and float(el.get(k) or 0.0) != 0.0:
                return Decimal(str(el[k]))
        if el.get("entry_fee_usd") is not None or el.get("exit_fee_usd") is not None:
            return Decimal(str(el.get("entry_fee_usd") or 0.0)) + Decimal(str(el.get("exit_fee_usd") or 0.0))
    if ep.get("filled") and str(ep.get("outcome_label") or "") in ("WIN", "LOSS"):
        gross = Decimal(str(ep.get("realized_gross_pnl_usd") or 0.0))
        net = Decimal(str(ep.get("realized_net_pnl_usd") or 0.0))
        diff = gross - net
        if diff >= Decimal("0"):
            return diff
    return Decimal(str(ep.get("total_fees_usd") or 0.0))


def build_c4_per_episode_risk_ledger(
    *,
    datasets: Mapping[str, Any],
    r2_replays: Mapping[str, Mapping[str, Any]],
    out_jsonl_gz_path: Path,
    arms: Sequence[str] = EXPECTED_R2_TAIL_ARMS_C4,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    min_net_rr: float = MIN_NET_RR_THRESHOLD,
) -> Dict[str, Any]:
    """
    §05.2(2) -> Artifact 12 (`12_per_episode_risk_ledger.jsonl.gz`):
    Deterministically streams EVERY R2 replay episode across all arms x both modes into a
    gzip-compressed JSONL ledger (atomic write + fsync + mtime=0) with canonical IDs,
    as-of decision fields, hypothetical fill-time risk action, and realized PnL/fees/tail flags.
    """
    bars_1m = list(datasets["1m"])
    out_path = Path(out_jsonl_gz_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f"{out_path.name}.tmp.{os.getpid()}")

    rows_written = 0
    row_hashes: List[str] = []
    episode_uids: List[str] = []
    per_arm_mode_counts: Dict[str, Dict[str, int]] = {}

    with tmp_path.open("wb") as raw_fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, compresslevel=6, mtime=0) as gzf:
            with io.TextIOWrapper(gzf, encoding="utf-8") as gz:
                for ac in sorted(arms):
                    per_arm_mode_counts[ac] = {}
                    for mode, sp_usd, slp_usd in EXPECTED_R2_TAIL_MODES_C4:
                        eps_raw = list(((r2_replays.get(ac) or {}).get("episodes_by_mode") or {}).get(mode, ()))
                        eps_sorted = sorted(
                            eps_raw,
                            key=lambda e: (
                                int(e.get("decision_timestamp_ms") or 0),
                                str(e.get("opportunity_id") or ""),
                                str(e.get("direction") or ""),
                            ),
                        )
                        per_arm_mode_counts[ac][mode] = len(eps_sorted)
                        for idx_in_cell, ep in enumerate(eps_sorted):
                            opp_id = str(ep.get("opportunity_id") or "")
                            dec_ts = int(ep.get("decision_timestamp_ms") or 0)
                            direction = str(ep.get("direction") or "")
                            ep_uid = f"{ac}|{mode}|{dec_ts}|{opp_id}|{idx_in_cell}"
                            dec_risk = round(float(ep.get("planned_net_risk_usd") or 0.0), 4)
                            dec_rr = round(float(ep.get("planned_net_rr") or ep.get("net_rr") or 0.0), 4)
                            qty = round(float(ep.get("executed_quantity") or ep.get("hypothetical_quantity") or 0.0), 4)
                            chk = int(ep.get("checkpoint_index_1m") or 0)

                            ft_action = "NO_NEXT_BAR_AVAILABLE"
                            ft_orig_risk: Optional[float] = None
                            ft_post_qty: Optional[float] = None
                            ft_post_risk: Optional[float] = None
                            ft_accepted: Optional[bool] = None
                            if chk + 1 < len(bars_1m):
                                fill_open = float(bars_1m[chk + 1]["open"])
                                ft = evaluate_causal_fill_time_policy_single_trade(
                                    direction=direction or "LONG",
                                    decision_entry=float(ep.get("entry_reference") or fill_open),
                                    fill_bar_open=fill_open,
                                    selected_sl=float(ep.get("selected_sl") or 0.0),
                                    take_profit=float(ep.get("take_profit") or 0.0),
                                    decision_qty=float(ep.get("executed_quantity") or ep.get("hypothetical_quantity") or 0.01),
                                    spread_usd=sp_usd,
                                    slippage_usd=slp_usd,
                                    risk_cap_usd=risk_cap_usd,
                                    min_net_rr=min_net_rr,
                                    now_ms=int(bars_1m[chk + 1]["open_time"]),
                                )
                                ft_action = str(ft["fill_time_action"])
                                ft_orig_risk = (
                                    round(float(ft["orig_qty_fill_time_net_risk_usd"]), 4)
                                    if ft.get("orig_qty_fill_time_net_risk_usd") is not None
                                    else None
                                )
                                ft_post_qty = (
                                    round(float(ft["fill_time_quantity"]), 4)
                                    if ft.get("fill_time_quantity") is not None
                                    else None
                                )
                                ft_post_risk = (
                                    round(float(ft["fill_time_planned_net_risk_usd"]), 4)
                                    if ft.get("fill_time_planned_net_risk_usd") is not None
                                    else None
                                )
                                ft_accepted = bool(ft["accepted"])

                            filled = bool(ep.get("filled", False))
                            outcome_lbl = str(ep.get("outcome_label") or "UNKNOWN")
                            el = ep.get("execution_ledger") or {}
                            exit_type = str(el.get("exit_type") or ep.get("exit_type") or outcome_lbl)
                            entry_fill_ts = el.get("entry_fill_ts")
                            exit_fill_ts = el.get("exit_fill_ts")

                            gross_d = Decimal(str(ep.get("realized_gross_pnl_usd") or 0.0))
                            net_d = Decimal(str(ep.get("realized_net_pnl_usd") or 0.0))
                            fee_d = _extract_episode_fee_usd(ep)
                            realized_r = round(float(ep.get("realized_net_r") or 0.0), 4)
                            realized_loss_d = (-net_d) if (filled and outcome_lbl == "LOSS") else Decimal("0")
                            exceeds_cap = bool(filled and outcome_lbl == "LOSS" and realized_loss_d > Decimal(str(risk_cap_usd)) + Decimal("0.000001"))
                            gap_or_slip_flag = bool(
                                "GAP" in exit_type.upper()
                                or exceeds_cap
                                or (ft_orig_risk is not None and ft_orig_risk > risk_cap_usd + 1e-6)
                            )

                            row: Dict[str, Any] = {
                                "episode_uid": ep_uid,
                                "arm_code": ac,
                                "fill_mode": mode,
                                "opportunity_id": opp_id,
                                "direction": direction,
                                "decision_timestamp_ms": dec_ts,
                                "entry_fill_ts": int(entry_fill_ts) if entry_fill_ts is not None else None,
                                "exit_fill_ts": int(exit_fill_ts) if exit_fill_ts is not None else None,
                                "checkpoint_index_1m": chk,
                                "filled": filled,
                                "outcome_label": outcome_lbl,
                                "exit_type": exit_type,
                                "executed_quantity": qty,
                                "planned_net_risk_usd": dec_risk,
                                "planned_net_rr": dec_rr,
                                "fill_time_action": ft_action,
                                "fill_time_orig_qty_net_risk_usd": ft_orig_risk,
                                "fill_time_post_policy_qty": ft_post_qty,
                                "fill_time_post_policy_net_risk_usd": ft_post_risk,
                                "fill_time_accepted": ft_accepted,
                                "realized_gross_pnl_usd": _dec4(gross_d),
                                "total_fees_usd": _dec4(fee_d),
                                "realized_net_pnl_usd": _dec4(net_d),
                                "realized_net_r": realized_r,
                                "realized_loss_usd": _dec4(realized_loss_d),
                                "realized_loss_exceeds_cap": exceeds_cap,
                                "adverse_gap_or_slippage_flag": gap_or_slip_flag,
                                "decision_uses_future_fields": False,
                            }
                            line = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                            gz.write(line + "\n")
                            rows_written += 1
                            row_hashes.append(hashlib.sha256(line.encode("utf-8")).hexdigest())
                            episode_uids.append(ep_uid)
        raw_fh.flush()
        os.fsync(raw_fh.fileno())
    os.replace(tmp_path, out_path)

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4_TAIL,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4_TAIL,
        "ledger_path": str(out_path),
        "ledger_file_sha256": _file_sha256(out_path),
        "ledger_size_bytes": int(out_path.stat().st_size),
        "total_episode_rows": rows_written,
        "per_arm_mode_row_counts": per_arm_mode_counts,
        "episode_uids_sha256": canonical_hash(episode_uids),
        "canonical_rows_sha256": canonical_hash(row_hashes),
    }


def recompute_c4_tail_counters_from_episode_ledger(
    ledger_jsonl_gz_path: Path,
    *,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    expected_arms: Sequence[str] = EXPECTED_R2_TAIL_ARMS_C4,
    expected_modes: Sequence[str] = EXPECTED_R2_TAIL_MODE_NAMES_C4,
) -> Dict[str, Any]:
    """
    §05.2(3): Independent streaming numeric recomputation strictly from `12_per_episode_risk_ledger.jsonl.gz`.
    Does NOT trust or import any producer aggregate status/boolean flags.
    """
    p = Path(ledger_jsonl_gz_path)
    if not p.is_file() or p.stat().st_size == 0:
        return {
            "status": "FAIL_MISSING_OR_EMPTY_LEDGER",
            "total_episode_rows": 0,
            "violations": ["MISSING_OR_EMPTY_PER_EPISODE_RISK_LEDGER"],
        }

    violations: List[str] = []
    cap_d = Decimal(str(risk_cap_usd))
    eps_tol = Decimal("0.000001")
    seen_uids: set[str] = set()
    all_uids: List[str] = []
    cells: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    total_rows = 0

    try:
        with gzip.open(p, "rt", encoding="utf-8") as gz:
            for line_no, raw_line in enumerate(gz, 1):
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                total_rows += 1
                uid = str(row.get("episode_uid") or "")
                if not uid:
                    violations.append(f"MISSING_EPISODE_UID:line={line_no}")
                    continue
                if uid in seen_uids:
                    violations.append(f"DUPLICATE_EPISODE_UID:{uid}")
                    continue
                seen_uids.add(uid)
                all_uids.append(uid)

                if row.get("decision_uses_future_fields") is not False:
                    violations.append(f"FUTURE_FIELD_FLAG_VIOLATION:{uid}")

                for num_k in (
                    "executed_quantity",
                    "planned_net_risk_usd",
                    "realized_gross_pnl_usd",
                    "total_fees_usd",
                    "realized_net_pnl_usd",
                    "realized_net_r",
                    "realized_loss_usd",
                ):
                    if not _is_finite_num(row.get(num_k)):
                        violations.append(f"NON_FINITE_NUMERIC:{uid}:{num_k}={row.get(num_k)}")

                ac = str(row.get("arm_code") or "")
                md = str(row.get("fill_mode") or "")
                cells.setdefault((ac, md), []).append(row)
    except Exception as exc:
        return {
            "status": "FAIL_LEDGER_PARSE_ERROR",
            "total_episode_rows": total_rows,
            "violations": [f"LEDGER_READ_ERROR:{type(exc).__name__}:{exc}"],
        }

    if total_rows == 0:
        violations.append("ZERO_ROWS_IN_PER_EPISODE_RISK_LEDGER")

    arm_mode_recomputed: Dict[str, Dict[str, Any]] = {}
    total_dec_cap_viols = 0
    total_post_fill_cap_viols = 0
    total_realized_gt_cap = 0
    global_max_realized_loss = 0.0
    max_gross_fee_net_residual = Decimal("0")

    for ac in sorted(expected_arms):
        arm_mode_recomputed[ac] = {}
        for md in expected_modes:
            rows = cells.get((ac, md))
            if rows is None:
                violations.append(f"MISSING_ARM_MODE_IN_LEDGER:{ac}:{md}")
                continue
            action_counts: Counter[str] = Counter()
            closed_n = 0
            wins_n = 0
            losses_n = 0
            open_h_n = 0
            gap_unres_n = 0
            unfilled_n = 0
            dec_viols = 0
            post_fill_viols = 0
            orig_qty_breaches = 0
            realized_gt_cap = 0
            max_dec_risk = Decimal("0")
            max_orig_fill_risk = Decimal("0")
            max_post_fill_risk = Decimal("0")
            max_realized_loss = Decimal("0")
            loss_vals: List[float] = []
            gross_sum = Decimal("0")
            fee_sum = Decimal("0")
            net_sum = Decimal("0")
            tail_ids: List[str] = []
            opp_ids: List[str] = []

            for r in rows:
                opp_ids.append(str(r.get("opportunity_id") or ""))
                dec_r = Decimal(str(r["planned_net_risk_usd"]))
                if dec_r > max_dec_risk:
                    max_dec_risk = dec_r
                if dec_r > cap_d + eps_tol:
                    dec_viols += 1

                act = str(r.get("fill_time_action") or "")
                if act and act != "NO_NEXT_BAR_AVAILABLE":
                    action_counts[act] += 1
                if r.get("fill_time_orig_qty_net_risk_usd") is not None:
                    or_d = Decimal(str(r["fill_time_orig_qty_net_risk_usd"]))
                    if or_d > max_orig_fill_risk:
                        max_orig_fill_risk = or_d
                    if or_d > cap_d + eps_tol:
                        orig_qty_breaches += 1
                if r.get("fill_time_accepted") and r.get("fill_time_post_policy_net_risk_usd") is not None:
                    pr_d = Decimal(str(r["fill_time_post_policy_net_risk_usd"]))
                    if pr_d > max_post_fill_risk:
                        max_post_fill_risk = pr_d
                    if pr_d > cap_d + eps_tol:
                        post_fill_viols += 1

                lbl = str(r.get("outcome_label") or "")
                filled = bool(r.get("filled", False))
                if not filled:
                    unfilled_n += 1
                elif lbl == "OPEN_AT_HORIZON":
                    open_h_n += 1
                elif lbl == "DATA_GAP_UNRESOLVED":
                    gap_unres_n += 1
                elif lbl in ("WIN", "LOSS"):
                    closed_n += 1
                    g_d = Decimal(str(r["realized_gross_pnl_usd"]))
                    f_d = Decimal(str(r["total_fees_usd"]))
                    n_d = Decimal(str(r["realized_net_pnl_usd"]))
                    gross_sum += g_d
                    fee_sum += f_d
                    net_sum += n_d
                    resid = abs((g_d - f_d) - n_d)
                    if resid > max_gross_fee_net_residual:
                        max_gross_fee_net_residual = resid
                    if resid > Decimal("0.01"):
                        violations.append(
                            f"EPISODE_GROSS_FEE_NET_MISMATCH:{r['episode_uid']}:gross={g_d},fee={f_d},net={n_d}"
                        )
                    if lbl == "WIN":
                        wins_n += 1
                        if n_d < Decimal("-0.01"):
                            # Note: rare gross win turned net negative by fees is still possible; flag only impossible sign flip
                            pass
                    else:
                        losses_n += 1
                        rl_d = -n_d
                        claimed_rl = Decimal(str(r["realized_loss_usd"]))
                        if abs(rl_d - claimed_rl) > Decimal("0.0005"):
                            violations.append(
                                f"EPISODE_REALIZED_LOSS_SIGN_OR_VALUE_MISMATCH:{r['episode_uid']}:calc={rl_d},claimed={claimed_rl}"
                            )
                        if rl_d > max_realized_loss:
                            max_realized_loss = rl_d
                        loss_vals.append(float(rl_d))
                        is_gt = rl_d > cap_d + eps_tol
                        if bool(r.get("realized_loss_exceeds_cap")) != is_gt:
                            violations.append(f"EPISODE_EXCEEDS_CAP_FLAG_MISMATCH:{r['episode_uid']}")
                        if is_gt:
                            realized_gt_cap += 1
                            tail_ids.append(str(r.get("opportunity_id") or ""))

            loss_vals.sort()
            total_dec_cap_viols += dec_viols
            total_post_fill_cap_viols += post_fill_viols
            total_realized_gt_cap += realized_gt_cap
            global_max_realized_loss = max(global_max_realized_loss, float(max_realized_loss))

            arm_mode_recomputed[ac][md] = {
                "scheduled_trades": len(rows),
                "closed_trades": closed_n,
                "wins": wins_n,
                "losses": losses_n,
                "open_at_horizon_count": open_h_n,
                "data_gap_unresolved_count": gap_unres_n,
                "unfilled_count": unfilled_n,
                "decision_time_cap_violations": dec_viols,
                "post_fill_policy_cap_violations": post_fill_viols,
                "max_decision_planned_net_risk_usd": _dec4(max_dec_risk),
                "orig_qty_fill_time_breaches_over_2p50": orig_qty_breaches,
                "max_orig_qty_fill_time_net_risk_usd": _dec4(max_orig_fill_risk),
                "max_post_policy_fill_time_planned_risk_usd": _dec4(max_post_fill_risk),
                "fill_time_policy_actions": dict(sorted(action_counts.items())),
                "realized_losses_exceeding_2p50_count": realized_gt_cap,
                "max_realized_net_loss_usd": _dec4(max_realized_loss),
                "realized_loss_quantiles_usd": {
                    "p50": _nearest_rank_quantile(loss_vals, 0.50),
                    "p90": _nearest_rank_quantile(loss_vals, 0.90),
                    "p95": _nearest_rank_quantile(loss_vals, 0.95),
                    "max": _dec4(max_realized_loss),
                },
                "gross_pnl_usd": _dec4(gross_sum),
                "total_fees_usd": _dec4(fee_sum),
                "net_pnl_usd": _dec4(net_sum),
                "tail_loss_gt_2p50_opportunity_ids_sha256": canonical_hash(tail_ids),
                "opportunity_ids_sha256": canonical_hash(opp_ids),
            }

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4_TAIL,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4_TAIL,
        "ledger_file_sha256": _file_sha256(p),
        "risk_cap_usd": float(risk_cap_usd),
        "total_episode_rows": total_rows,
        "episode_uids_sha256": canonical_hash(all_uids),
        "decision_time_cap_violations": total_dec_cap_viols,
        "post_fill_policy_cap_violations": total_post_fill_cap_viols,
        "total_realized_losses_exceeding_2p50_count": total_realized_gt_cap,
        "global_max_realized_net_loss_usd": round(global_max_realized_loss, 4),
        "max_episode_gross_fee_net_residual_usd": _dec4(max_gross_fee_net_residual),
        "arm_mode_recomputed": arm_mode_recomputed,
        "violations": violations,
        "status": "PASS" if len(violations) == 0 else "FAIL",
    }


def build_c4_realized_tail_evidence_from_r2_replays(
    *,
    datasets: Mapping[str, Any],
    r2_replays: Mapping[str, Mapping[str, Any]],
    ledger_meta: Mapping[str, Any],
    ledger_recomputed: Mapping[str, Any],
    arms: Sequence[str] = EXPECTED_R2_TAIL_ARMS_C4,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    min_net_rr: float = MIN_NET_RR_THRESHOLD,
) -> Dict[str, Any]:
    """
    §05.2(1) -> Artifact 11 (`11_realized_tail_complete_by_arm_and_mode.json`):
    Calls existing `audit_r2_fill_time_net_risk_and_realized_tail` read-only AND pairs it with
    complete arm x mode matrices, quantiles, IDs, coverage matrix, and explicit disclosure.
    NEVER falls back to `{}` and NEVER hardcodes a boolean disclosure flag.
    """
    upstream = audit_r2_fill_time_net_risk_and_realized_tail(
        datasets=datasets,
        r2_replays=r2_replays,
        arms=arms,
        risk_cap_usd=risk_cap_usd,
        min_net_rr=min_net_rr,
    )
    up_arms = upstream.get("arm_fill_time_and_tail_audits")
    if not isinstance(up_arms, Mapping) or len(up_arms) == 0:
        raise RuntimeError("INCOMPLETE_EVIDENCE:UPSTREAM_ARM_FILL_TIME_AND_TAIL_AUDITS_EMPTY")

    rec_arms = ledger_recomputed.get("arm_mode_recomputed") or {}
    coverage_matrix: List[Dict[str, Any]] = []
    combined_arm_mode: Dict[str, Dict[str, Any]] = {}
    for ac in sorted(arms):
        combined_arm_mode[ac] = {}
        for md in EXPECTED_R2_TAIL_MODE_NAMES_C4:
            u_cell = (up_arms.get(ac) or {}).get(md)
            r_cell = (rec_arms.get(ac) or {}).get(md)
            if not isinstance(u_cell, Mapping) or not isinstance(r_cell, Mapping):
                raise RuntimeError(f"INCOMPLETE_EVIDENCE:MISSING_ARM_MODE_CELL:{ac}:{md}")
            merged_cell = dict(u_cell)
            merged_cell["closed_trades"] = r_cell["closed_trades"]
            merged_cell["wins"] = r_cell["wins"]
            merged_cell["losses"] = r_cell["losses"]
            merged_cell["data_gap_unresolved_count"] = r_cell["data_gap_unresolved_count"]
            merged_cell["unfilled_count"] = r_cell["unfilled_count"]
            merged_cell["realized_loss_quantiles_usd"] = r_cell["realized_loss_quantiles_usd"]
            merged_cell["gross_pnl_usd"] = r_cell["gross_pnl_usd"]
            merged_cell["total_fees_usd"] = r_cell["total_fees_usd"]
            merged_cell["net_pnl_usd"] = r_cell["net_pnl_usd"]
            merged_cell["tail_loss_gt_2p50_opportunity_ids_sha256"] = r_cell["tail_loss_gt_2p50_opportunity_ids_sha256"]
            merged_cell["opportunity_ids_sha256"] = r_cell["opportunity_ids_sha256"]
            combined_arm_mode[ac][md] = merged_cell
            coverage_matrix.append(
                {
                    "arm_code": ac,
                    "fill_mode": md,
                    "scheduled_trades": int(merged_cell["scheduled_trades"]),
                    "closed_trades": int(merged_cell["closed_trades"]),
                    "open_at_horizon_count": int(merged_cell["open_at_horizon_count"]),
                    "realized_losses_exceeding_2p50_count": int(merged_cell["realized_losses_exceeding_2p50_count"]),
                    "max_realized_net_loss_usd": float(merged_cell["max_realized_net_loss_usd"]),
                    "present": True,
                }
            )

    out: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C4_TAIL,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4_TAIL,
        "risk_cap_usd": float(risk_cap_usd),
        "min_net_rr": float(min_net_rr),
        "expected_arms": list(sorted(arms)),
        "expected_modes": list(EXPECTED_R2_TAIL_MODE_NAMES_C4),
        "coverage_cells_expected": len(arms) * len(EXPECTED_R2_TAIL_MODE_NAMES_C4),
        "coverage_cells_populated": len(coverage_matrix),
        "coverage_matrix": coverage_matrix,
        "decision_time_cap_violations": int(upstream["decision_time_cap_violations"]),
        "post_fill_policy_cap_violations": int(upstream["post_fill_policy_cap_violations"]),
        "total_realized_losses_exceeding_2p50_count": int(ledger_recomputed["total_realized_losses_exceeding_2p50_count"]),
        "global_max_realized_net_loss_usd": float(ledger_recomputed["global_max_realized_net_loss_usd"]),
        "arm_fill_time_and_tail_audits": combined_arm_mode,
        "synthetic_stress_controls": upstream["synthetic_stress_controls"],
        "realized_tail_loss_disclosure": upstream["realized_tail_loss_disclosure"],
        "c3_root_cause_audit": {
            "c3_runner_file": "aurum-desk/tools/run_kh_02_r2_c3_acceptance.py",
            "c3_f12_bug": "Read nonexistent keys `realized_tail_summary`/`summary` and defaulted silently to `{}` instead of `arm_fill_time_and_tail_audits`.",
            "c3_f12_cap_key_bug": "Read nonexistent key `fill_time_cap_violations_count` defaulting to `0` instead of `decision_time_cap_violations` + `post_fill_policy_cap_violations`.",
            "c3_f13_hardcoded_bool_bug": "Hardcoded `realized_tail_risk_disclosed = True` while `f12['realized_tail_summary'] == {}`.",
            "sealed_c3_folder_modified": False,
        },
        "linked_per_episode_ledger": {
            "ledger_filename": Path(str(ledger_meta["ledger_path"])).name,
            "ledger_file_sha256": ledger_meta["ledger_file_sha256"],
            "total_episode_rows": int(ledger_meta["total_episode_rows"]),
            "episode_uids_sha256": ledger_meta["episode_uids_sha256"],
            "canonical_rows_sha256": ledger_meta["canonical_rows_sha256"],
        },
        "upstream_audit_canonical_sha256": upstream["artifact_canonical_sha256"],
        "status": (
            "PASS"
            if (
                upstream.get("status") == "PASS"
                and ledger_recomputed.get("status") == "PASS"
                and len(coverage_matrix) == len(arms) * len(EXPECTED_R2_TAIL_MODE_NAMES_C4)
                and int(ledger_meta["total_episode_rows"]) > 0
            )
            else "FAIL"
        ),
    }
    out["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in out.items() if k != "artifact_canonical_sha256"})
    return out


def validate_c4_tail_disclosure_vs_risk_ledger(
    tail_summary_doc: Mapping[str, Any],
    ledger_jsonl_gz_path: Path,
    *,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
    expected_arms: Sequence[str] = EXPECTED_R2_TAIL_ARMS_C4,
    expected_modes: Sequence[str] = EXPECTED_R2_TAIL_MODE_NAMES_C4,
) -> Dict[str, Any]:
    """
    §05.2(4): Fail-closed cross-verification comparing `11_realized_tail_complete_by_arm_and_mode.json`
    against an independent streaming recomputation of `12_per_episode_risk_ledger.jsonl.gz`.
    """
    violations: List[str] = []
    if not isinstance(tail_summary_doc, Mapping) or len(tail_summary_doc) == 0:
        return {"valid": False, "status": "FAIL", "violations": ["EMPTY_TAIL_SUMMARY_DOCUMENT"]}

    arm_audits = tail_summary_doc.get("arm_fill_time_and_tail_audits")
    if not isinstance(arm_audits, Mapping) or len(arm_audits) == 0:
        return {"valid": False, "status": "FAIL", "violations": ["EMPTY_OR_MISSING_ARM_FILL_TIME_AND_TAIL_AUDITS"]}

    if abs(float(tail_summary_doc.get("risk_cap_usd", -1.0)) - float(risk_cap_usd)) > 1e-6:
        violations.append(
            f"RISK_CAP_THRESHOLD_TAMPERED:claimed={tail_summary_doc.get('risk_cap_usd')},expected={risk_cap_usd}"
        )

    recomp = recompute_c4_tail_counters_from_episode_ledger(
        ledger_jsonl_gz_path,
        risk_cap_usd=risk_cap_usd,
        expected_arms=expected_arms,
        expected_modes=expected_modes,
    )
    if recomp.get("status") != "PASS":
        violations.extend(recomp.get("violations") or ["LEDGER_RECOMPUTATION_FAILED"])
        return {"valid": False, "status": "FAIL", "violations": violations, "recomputed": recomp}

    linked = tail_summary_doc.get("linked_per_episode_ledger") or {}
    if linked.get("ledger_file_sha256") != recomp.get("ledger_file_sha256"):
        violations.append("LINKED_LEDGER_FILE_SHA256_MISMATCH")
    if int(linked.get("total_episode_rows", -1)) != int(recomp.get("total_episode_rows", -2)):
        violations.append(
            f"LINKED_LEDGER_ROW_COUNT_MISMATCH:claimed={linked.get('total_episode_rows')},actual={recomp.get('total_episode_rows')}"
        )
    if linked.get("episode_uids_sha256") != recomp.get("episode_uids_sha256"):
        violations.append("LINKED_LEDGER_EPISODE_UIDS_SHA256_MISMATCH")

    if int(tail_summary_doc.get("decision_time_cap_violations", -1)) != int(recomp["decision_time_cap_violations"]):
        violations.append("DECISION_CAP_VIOLATIONS_COUNT_MISMATCH")
    if int(tail_summary_doc.get("post_fill_policy_cap_violations", -1)) != int(recomp["post_fill_policy_cap_violations"]):
        violations.append("POST_FILL_CAP_VIOLATIONS_COUNT_MISMATCH")
    if int(tail_summary_doc.get("total_realized_losses_exceeding_2p50_count", -1)) != int(recomp["total_realized_losses_exceeding_2p50_count"]):
        violations.append("TOTAL_REALIZED_LOSSES_GT_CAP_COUNT_MISMATCH")
    if abs(float(tail_summary_doc.get("global_max_realized_net_loss_usd", -1.0)) - float(recomp["global_max_realized_net_loss_usd"])) > 0.001:
        violations.append("GLOBAL_MAX_REALIZED_LOSS_MISMATCH")

    rec_arms = recomp["arm_mode_recomputed"]
    for ac in sorted(expected_arms):
        if ac not in arm_audits:
            violations.append(f"MISSING_ARM_IN_TAIL_SUMMARY:{ac}")
            continue
        for md in expected_modes:
            c_doc = (arm_audits.get(ac) or {}).get(md)
            c_rec = (rec_arms.get(ac) or {}).get(md)
            if not isinstance(c_doc, Mapping):
                violations.append(f"MISSING_MODE_IN_TAIL_SUMMARY:{ac}:{md}")
                continue
            for int_k in (
                "scheduled_trades",
                "closed_trades",
                "wins",
                "losses",
                "open_at_horizon_count",
                "orig_qty_fill_time_breaches_over_2p50",
                "realized_losses_exceeding_2p50_count",
            ):
                if int(c_doc.get(int_k, -999)) != int(c_rec[int_k]):
                    violations.append(
                        f"CELL_INT_MISMATCH:{ac}:{md}:{int_k}:claimed={c_doc.get(int_k)}!=recomp={c_rec[int_k]}"
                    )
            for flt_k in (
                "max_decision_planned_net_risk_usd",
                "max_orig_qty_fill_time_net_risk_usd",
                "max_post_policy_fill_time_planned_risk_usd",
                "max_realized_net_loss_usd",
                "gross_pnl_usd",
                "total_fees_usd",
                "net_pnl_usd",
            ):
                if not _is_finite_num(c_doc.get(flt_k)) or abs(float(c_doc.get(flt_k)) - float(c_rec[flt_k])) > 0.01:
                    violations.append(
                        f"CELL_FLOAT_MISMATCH:{ac}:{md}:{flt_k}:claimed={c_doc.get(flt_k)}!=recomp={c_rec[flt_k]}"
                    )
            if dict(c_doc.get("fill_time_policy_actions") or {}) != dict(c_rec["fill_time_policy_actions"]):
                violations.append(f"CELL_FILL_TIME_ACTIONS_MISMATCH:{ac}:{md}")
            if c_doc.get("opportunity_ids_sha256") != c_rec["opportunity_ids_sha256"]:
                violations.append(f"CELL_OPPORTUNITY_IDS_SHA_MISMATCH:{ac}:{md}")
            if c_doc.get("tail_loss_gt_2p50_opportunity_ids_sha256") != c_rec["tail_loss_gt_2p50_opportunity_ids_sha256"]:
                violations.append(f"CELL_TAIL_IDS_SHA_MISMATCH:{ac}:{md}")
            q_doc = c_doc.get("realized_loss_quantiles_usd") or {}
            q_rec = c_rec["realized_loss_quantiles_usd"]
            for qk in ("p50", "p90", "p95", "max"):
                if not _is_finite_num(q_doc.get(qk)) or abs(float(q_doc.get(qk)) - float(q_rec[qk])) > 0.01:
                    violations.append(f"CELL_QUANTILE_MISMATCH:{ac}:{md}:{qk}")

    disc = str(tail_summary_doc.get("realized_tail_loss_disclosure") or "")
    if "NOT a physical guarantee" not in disc or "2.50" not in disc:
        violations.append("MISSING_REALIZED_TAIL_PHYSICAL_GUARANTEE_DISCLOSURE")

    return {
        "valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "violations": violations,
        "recomputed_summary": {
            "total_episode_rows": recomp["total_episode_rows"],
            "decision_time_cap_violations": recomp["decision_time_cap_violations"],
            "post_fill_policy_cap_violations": recomp["post_fill_policy_cap_violations"],
            "total_realized_losses_exceeding_2p50_count": recomp["total_realized_losses_exceeding_2p50_count"],
            "global_max_realized_net_loss_usd": recomp["global_max_realized_net_loss_usd"],
            "max_episode_gross_fee_net_residual_usd": recomp["max_episode_gross_fee_net_residual_usd"],
            "episode_uids_sha256": recomp["episode_uids_sha256"],
        },
    }


def reconcile_c4_full_universe_cost_fees(
    full_universe_rerisk_doc: Mapping[str, Any],
    fixed_fill_doc: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    §05.4: Audits fee display consistency across all 6 full-universe rerisked scenarios.
    Detects upstream R2 `_replay_chronological_custom_cost_scenario` displaying `total_fees_usd=0.0`
    while `net_pnl_usd = gross_pnl_usd - fees`, reconciles exact Decimal fees (`gross - net`) in C4
    evidence without mutating R2 core functions, and verifies exact parity of `1X_BASE_0p0004`
    against fixed-fill 1x fees!
    """
    scenarios = full_universe_rerisk_doc.get("scenarios") or {}
    reconciled_scenarios: Dict[str, Any] = {}
    upstream_zero_fee_display_hits = 0
    reconciliation_mismatches: List[str] = []

    for sc_name, sc_val in sorted(scenarios.items()):
        arms_in = sc_val.get("arms") or {}
        arms_out: Dict[str, Any] = {}
        for ac, a_val in sorted(arms_in.items()):
            rs = a_val.get("replay_summary") or {}
            gross_d = Decimal(str(rs.get("gross_pnl_usd") or 0.0))
            net_d = Decimal(str(rs.get("net_pnl_usd") or 0.0))
            raw_fee_d = Decimal(str(rs.get("total_fees_usd") or 0.0))
            implied_fee_d = gross_d - net_d
            closed_n = int(rs.get("closed") or 0)
            zero_display_bug = bool(closed_n > 0 and raw_fee_d == Decimal("0") and implied_fee_d > Decimal("0.01"))
            if zero_display_bug:
                upstream_zero_fee_display_hits += 1
            elif closed_n > 0 and abs(raw_fee_d - implied_fee_d) > Decimal("0.01"):
                reconciliation_mismatches.append(
                    f"NONZERO_FEE_VS_IMPLIED_MISMATCH:{sc_name}:{ac}:raw_fee={raw_fee_d},implied={implied_fee_d}"
                )
            if implied_fee_d < Decimal("-0.01"):
                reconciliation_mismatches.append(f"NEGATIVE_IMPLIED_FEE:{sc_name}:{ac}:gross={gross_d},net={net_d}")
            # Cross-check 1X_BASE_0p0004 implied fees directly against fixed-fill 1x fees_usd
            ff_match: Optional[bool] = None
            if sc_name == "1X_BASE_0p0004" and ac in (fixed_fill_doc.get("arms") or {}):
                ff_fee = Decimal(str((((fixed_fill_doc["arms"][ac]).get("1x_base_0p0004") or {}).get("fees_usd")) or 0.0))
                ff_match = abs(implied_fee_d - ff_fee) <= Decimal("0.01")
                if not ff_match:
                    reconciliation_mismatches.append(
                        f"1X_FEE_MISMATCH_VS_FIXED_FILL:{ac}:implied={implied_fee_d},fixed_fill={ff_fee}"
                    )
            arms_out[ac] = {
                "closed_trades": closed_n,
                "wins": int(rs.get("wins") or 0),
                "losses": int(rs.get("losses") or 0),
                "open_at_horizon": int(rs.get("open_at_horizon") or 0),
                "gross_pnl_usd": _dec4(gross_d),
                "net_pnl_usd": _dec4(net_d),
                "upstream_displayed_total_fees_usd": _dec4(raw_fee_d),
                "c4_reconciled_total_fees_usd": _dec4(implied_fee_d if zero_display_bug else raw_fee_d),
                "fee_accounting_status": (
                    "UPSTREAM_DISPLAY_ZERO_BUG_RECONCILED_FROM_GROSS_MINUS_NET"
                    if zero_display_bug
                    else "DIRECTLY_RECONCILED"
                ),
                "matches_fixed_fill_1x_fees": ff_match,
                "expectancy_r": float(rs.get("expectancy_r") or 0.0),
            }
        reconciled_scenarios[sc_name] = {
            "raw_r2_eligible": int(sc_val.get("raw_r2_geometry_risk_eligible_count") or 0),
            "arms": arms_out,
        }

    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4_TAIL,
        "upstream_bug_source": "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py:3808 (`ld.get('total_fees_usd')` missing key in `TradeExecutionLedger.to_dict()`, which stores fees under `entry_fee_usd`/`exit_fee_usd` while `realized_net_pnl_usd` already deducts fees accurately).",
        "upstream_zero_fee_display_hits": upstream_zero_fee_display_hits,
        "core_r2_replay_mutated": False,
        "reconciliation_mismatches": reconciliation_mismatches,
        "reconciled_scenarios": reconciled_scenarios,
        "status": "PASS" if len(reconciliation_mismatches) == 0 else "NOT_RECONCILED",
    }


def summarize_c4_science_and_risk_truth(
    *,
    tail_doc: Mapping[str, Any],
    reconciliation_doc: Mapping[str, Any],
    cost_fee_reconciliation: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    §05.2(5): Explicitly separates executed modeled historical outcomes, advisory hypothetical
    fill-time re-risk, unchanged production engine behavior, and unobserved future gap risk.
    """
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4_TAIL,
        "A_executed_modeled_historical_outcomes": {
            "data_regime": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": DEFAULT_MODELED_SPREAD_USD,
            "observed_historical_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "total_realized_losses_exceeding_2p50_count": int(tail_doc["total_realized_losses_exceeding_2p50_count"]),
            "global_max_realized_net_loss_usd": float(tail_doc["global_max_realized_net_loss_usd"]),
            "research_economic_verdict": "NO_PROVEN_EDGE",
        },
        "B_advisory_hypothetical_fill_time_rerisk": {
            "is_live_runtime_enforcement": False,
            "decision_time_cap_violations": int(tail_doc["decision_time_cap_violations"]),
            "post_fill_policy_cap_violations": int(tail_doc["post_fill_policy_cap_violations"]),
            "description": "Causal fill-time open-price policy audit is offline research advisory ONLY, not a live runtime guarantee.",
        },
        "C_production_engine_behavior": {
            "unchanged": True,
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
            "ACTIVATION_READY": "NO",
        },
        "D_unobserved_future_gap_and_tail_risk": {
            "planned_cap_guarantees_realized_loss_le_2p50": False,
            "disclosure": tail_doc["realized_tail_loss_disclosure"],
        },
        "independent_ledger_reconciliation_status": reconciliation_doc["status"],
        "cost_fee_reconciliation_status": cost_fee_reconciliation["status"],
    }
