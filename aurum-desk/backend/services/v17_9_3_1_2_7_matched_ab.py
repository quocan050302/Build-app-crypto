"""Injective, source-bound comparisons; independently verified from disk ledgers."""
from __future__ import annotations
import gzip
import json
import math
from pathlib import Path
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

INTENT_FIELDS = ("accepted", "status", "new_decision_timestamp_ms", "new_entry_reference",
                 "new_selected_sl", "new_take_profit", "new_quantity", "rejection_reason")
TRADE_FIELDS = ("entry_fill_ts", "exit_fill_ts", "entry_fill_price", "exit_fill_price",
                "outcome_label", "realized_net_pnl_usd", "entry_fee_usd", "exit_fee_usd")


def _index(rows):
    result = {}
    seen = set()
    for r in rows:
        oid, ts, source = r.get("opportunity_id"), r.get("canonical_decision_timestamp_ms", r.get("decision_timestamp_ms")), r.get("source_snapshot_hash")
        if not oid or not isinstance(ts, int) or isinstance(ts, bool) or ts <= 0 or not source:
            raise ValueError("MISSING_MATCH_IDENTITY")
        key = (str(oid), ts, str(source))
        if key in result or str(oid) in seen:
            raise ValueError("DUPLICATE_OPPORTUNITY_ID")
        seen.add(str(oid))
        result[key] = r
    return result


def _money(row):
    if row is None or row.get("outcome_label") not in ("WIN", "LOSS"):
        return 0., 0.
    fields = ("realized_gross_pnl_usd", "realized_net_pnl_usd", "entry_fee_usd", "exit_fee_usd")
    if any(row.get(k) is None or isinstance(row[k], bool) or not math.isfinite(float(row[k])) for k in fields):
        raise ValueError("MISSING_ACCOUNTING_COMPONENT")
    gross, net, ent, ext = (float(row[k]) for k in fields)
    if min(ent, ext)<0 or abs(gross-ent-ext-net)>.001:
        raise ValueError("FEE_NET_RECONCILIATION_MISMATCH")
    return net, float(row.get("realized_r", 0.))


def compute_policy_decision_diff_v1793127(rows_a, rows_b):
    a,b = _index(rows_a),_index(rows_b)
    identities_a={k[0]:k for k in a}
    identities_b={k[0]:k for k in b}
    for oid in identities_a.keys() & identities_b.keys():
        if identities_a[oid] != identities_b[oid]:
            raise ValueError("PAIR_SOURCE_OR_TIMESTAMP_MISMATCH")
    rows=[]
    for key in sorted(a.keys()|b.keys()):
        ra,rb=a.get(key),b.get(key)
        def intent(row):
            if row is None: return None
            result={k:row.get(k) for k in INTENT_FIELDS}
            old=row.get("old_plan") or {}
            for key in ("entry_reference","selected_sl","take_profit","quantity"):
                result["effective_"+key]=row.get("new_"+key) if row.get("new_"+key) is not None else old.get(key)
            return result
        ia,ib=intent(ra),intent(rb)
        kind="ONLY_IN_A" if rb is None else "ONLY_IN_B" if ra is None else "UNCHANGED" if ia==ib else "BOTH_DIFFERENT_ORDER_INTENT"
        rows.append({"opportunity_id":key[0],"decision_timestamp_ms":key[1],"source_snapshot_hash":key[2],
                     "classification":kind,"intent_a":ia,"intent_b":ib})
    changed=sum(r["classification"]!="UNCHANGED" for r in rows)
    return {"rows":rows,"rows_sha256":canonical_hash(rows),"changed_count":changed,
            "decision_relation":"SAME_ACTUAL_DECISIONS" if changed==0 else "ACTUAL_DECISION_DIFFERENCES"}


def compute_matched_policy_ab_v1793127(decisions_a, decisions_b, trades_a, trades_b, *, comparison_id, fill_mode):
    diff=compute_policy_decision_diff_v1793127(decisions_a,decisions_b)
    a,b=_index(trades_a),_index(trades_b)
    pairs=[]
    for key in sorted(a.keys()|b.keys()):
        ra,rb=a.get(key),b.get(key)
        na,xa=_money(ra)
        nb,xb=_money(rb)
        paired_closed=bool(ra and rb and ra.get("outcome_label") in ("WIN","LOSS") and rb.get("outcome_label") in ("WIN","LOSS"))
        pairs.append({"opportunity_id":key[0],"decision_timestamp_ms":key[1],"source_snapshot_hash":key[2],
                      "partition":"SAME_CANONICAL_OPPORTUNITY" if ra and rb else "ONLY_IN_A" if ra else "ONLY_IN_B",
                      "outcome_a":ra.get("outcome_label") if ra else None,"outcome_b":rb.get("outcome_label") if rb else None,
                      "trade_a":ra,"trade_b":rb,"paired_closed":paired_closed,
                      "paired_net_r_delta":xb-xa if paired_closed else None,
                      "net_usd_delta":round(nb-na,8)})
    deltas=[r["paired_net_r_delta"] for r in pairs if r["paired_closed"]]
    return {"comparison_id":comparison_id,"fill_mode":fill_mode,"pair_ledger":pairs,
            "pair_ledger_sha256":canonical_hash(pairs),"decision_diff":diff,
            "matched_closed_count":len(deltas),"paired_mean_net_r_delta":sum(deltas)/len(deltas) if deltas else None,
            "portfolio_net_usd_delta":round(sum(r["net_usd_delta"] for r in pairs),8),
            "incremental_edge_status":"INCREMENTAL_EDGE_NOT_DEMONSTRATED" if diff["changed_count"]==0 else "REQUIRES_STATISTICAL_VALIDATION",
            "relation":"IDENTICAL_ON_TESTED_DATA" if diff["changed_count"]==0 and all(r["net_usd_delta"]==0 for r in pairs) else "DIFFERENT_ON_TESTED_DATA"}


def read_jsonl(path):
    opener=gzip.open if str(path).endswith(".gz") else open
    with opener(path,"rt",encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def verify_matched_ab_against_raw_ledgers_v1793127(root: Path, spec):
    """Inputs must be separate serialized raw ledgers, never the claimed pair table."""
    try:
        paths=[]
        for key in ("decisions_a","decisions_b","trades_a","trades_b"):
            p=(Path(root)/spec[key]).resolve()
            if not p.is_relative_to(Path(root).resolve()):
                raise ValueError("FOREIGN_LEDGER_PATH")
            paths.append(p)
        # Independent row indexing, fee arithmetic, and pairing before comparison to claim.
        da,db,ta,tb=map(read_jsonl,paths)
        claim_path=(Path(root)/spec["claim"]).resolve()
        if not claim_path.is_relative_to(Path(root).resolve()):
            raise ValueError("FOREIGN_CLAIM_PATH")
        claim=json.loads(claim_path.read_text(encoding="utf-8"))
        for rows in (da,db,ta,tb):
            _index(rows)
        for rows in (ta,tb):
            for row in rows:
                _money(row)
        actual=compute_matched_policy_ab_v1793127(da,db,ta,tb,comparison_id=claim["comparison_id"],fill_mode=claim["fill_mode"])
        if actual!=claim:
            raise ValueError("MATCHED_PAIR_OR_DECISION_LEDGER_MISMATCH")
        return {"status":"PASS","pair_count":len(actual["pair_ledger"]),"pair_sha256":actual["pair_ledger_sha256"]}
    except (KeyError,ValueError,TypeError,OSError) as exc:
        return {"status":"FAIL","reason":str(exc)}
