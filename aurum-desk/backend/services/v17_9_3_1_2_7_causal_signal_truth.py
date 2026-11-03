"""Point-in-time research evidence. No orders, wall clock, or outcome inputs.

This module composes the existing structure/FVG/Unicorn detectors; it is not
a second strategy evaluator. Missing proof stays UNKNOWN in persisted output.
"""
from __future__ import annotations

import bisect
from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

VERSION = "17.9.3.1.2.7"
TF = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
      "1h": 3_600_000, "4h": 14_400_000}
TRUE, FALSE, UNKNOWN = "PROVEN_TRUE", "PROVEN_FALSE", "UNKNOWN"
SOURCE_IMPLEMENTATION_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False, default=str).encode()).hexdigest()


class Bar(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _closed(raw, duration, cutoff):
    result = []
    seen = {}
    for b in raw:
        op = _get(b, "open_time", _get(b, "timestamp"))
        cl = _get(b, "close_time", _get(b, "close_time_ms"))
        if isinstance(op, bool) or not isinstance(op, (int, float)) or op <= 0:
            continue
        # Deriving a bar's mathematical close is not inventing a signal known_at.
        cl = int(op) + duration if cl is None else cl
        if isinstance(cl, bool) or not isinstance(cl, (int, float)):
            continue
        if int(cl) != int(op) + duration or cl > cutoff or _get(b, "is_closed", True) is not True:
            continue
        try:
            vals = {k: float(_get(b, k)) for k in ("open", "high", "low", "close")}
            if not all(math.isfinite(v) and v > 0 for v in vals.values()):
                continue
            if not vals["low"] <= min(vals["open"], vals["close"]) <= max(vals["open"], vals["close"]) <= vals["high"]:
                continue
        except (TypeError, ValueError):
            continue
        row = Bar(timestamp=int(op), open_time=int(op), close_time=int(cl),
                  is_closed=True, **vals)
        if int(op) in seen and seen[int(op)] != row:
            raise ValueError("CONFLICTING_SOURCE_BAR")
        seen[int(op)] = row
    result.extend(seen[k] for k in sorted(seen))
    return result


@dataclass(frozen=True)
class CausalEvidenceRef:
    evidence_id: str
    evidence_type: str
    symbol: str
    timeframe: str
    source_bar_ids: tuple[str, ...]
    source_bar_open_ms: Optional[int]
    source_bar_close_ms: Optional[int]
    occurred_at_ms: Optional[int]
    confirmed_at_ms: Optional[int]
    known_at_ms: Optional[int]
    source_dataset_sha256: str
    source_row_hash: str
    quote_source_kind: str
    measured_value: Any
    proof_state: str
    reason_code: str
    evidence_derivation_version: str = VERSION
    source_bar_count: int = 0

    def __post_init__(self):
        if self.proof_state not in (TRUE, FALSE, UNKNOWN):
            raise ValueError("INVALID_PROOF_STATE")
        if self.proof_state != UNKNOWN:
            times = (self.source_bar_close_ms, self.confirmed_at_ms, self.known_at_ms)
            if any(isinstance(t, bool) or not isinstance(t, int) or t <= 0 for t in times):
                raise ValueError("MISSING_CAUSAL_TIMESTAMP")
            if not times[0] <= times[1] <= times[2] or not self.source_bar_ids:
                raise ValueError("IMPOSSIBLE_CAUSAL_REFERENCE")


@dataclass(frozen=True)
class VerifiedPreEntryFeatureSnapshot:
    opportunity_id: str
    setup_episode_id: str
    decision_timestamp_ms: int
    source_snapshot_hash: str
    features: tuple[CausalEvidenceRef, ...]
    derived_values: tuple[tuple[str, Any], ...]
    proof_hash: str

    def to_dict(self):
        return asdict(self)


_CACHE = OrderedDict()
_CACHE_HITS = 0
_CACHE_MISSES = 0


def evidence_cache_metrics():
    return {"hits": _CACHE_HITS, "misses": _CACHE_MISSES, "size": len(_CACHE), "max_size": 64}


def derive_pre_entry_features_from_causal_context_v1793127(
    decision: Mapping[str, Any], context: Any, *, dataset_sha256: str,
) -> dict:
    """Reconstruct features from closed OHLC, never from descriptor booleans/outcomes."""
    from services.liquidity_scenario_service import identify_confirmed_swings
    from services.htf_structure_service import evaluate_htf_structure
    from services.stop_placement_service import compute_stop_atr
    from services.unicorn_evidence_service import detect_unicorn_evidence
    import smc_engine

    global _CACHE_HITS, _CACHE_MISSES
    cutoff = int(_get(context, "decision_timestamp_ms", 0))
    if cutoff <= 0:
        raise ValueError("MISSING_DECISION_TIMESTAMP")
    symbol = str(_get(context, "symbol", "XAUUSDT"))
    direction = str(decision.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("INVALID_DIRECTION")
    series = {tf: _closed(_get(context, field, ()) or (), TF[tf], cutoff)
              for tf, field in (("1m", "closed_m1_candles"), ("5m", "closed_m5_candles"),
                                ("15m", "closed_m15_candles"), ("1h", "closed_h1_candles"),
                                ("4h", "closed_h4_candles"))}
    source_hash = canonical_hash({"bars": series, "dataset": dataset_sha256, "cutoff": cutoff,
                                  "version": VERSION, "code": SOURCE_IMPLEMENTATION_HASH,
                                  "policy": _get(context, "policy_config_snapshot", ()),
                                  "quote": {k:_get(context,k) for k in ("bid","ask","quote_timestamp_ms","quote_source_kind","quote_provider_source_id")},
                                  "symbol": symbol})
    cache_key = source_hash
    if cache_key in _CACHE:
        derived = _CACHE.pop(cache_key)
        _CACHE[cache_key] = derived
        _CACHE_HITS += 1
    else:
        _CACHE_MISSES += 1
        swings = {tf: identify_confirmed_swings(bars, timeframe=tf.upper(), symbol=symbol,
                                               as_of_ms=cutoff, left_bars=2, right_bars=2)
                  for tf, bars in series.items()}
        biases = {}
        for tf in ("4h", "1h", "15m", "5m"):
            snap = evaluate_htf_structure(series[tf], timeframe=tf.upper(), as_of_ms=cutoff, symbol=symbol)
            biases[tf] = {"bias": str(snap.bias), "status": str(snap.data_status)}
        bars = series["1m"]
        atr = compute_stop_atr(bars, as_of_ms=cutoff, symbol=symbol)
        fvgs = smc_engine.detect_fvgs(bars, timeframe="1M") if len(bars) >= 3 else []
        derived = {"swings": swings, "biases": biases, "atr": atr, "fvgs": fvgs}
        _CACHE[cache_key] = derived
        if len(_CACHE) > 64:
            _CACHE.popitem(last=False)
    bars = series["1m"]
    sh, sl = derived["swings"]["1m"]
    quote_kind = str(_get(context, "quote_source_kind", "UNKNOWN"))
    if quote_kind == "REAL_PROVIDER_QUOTE" and not _get(context, "quote_provider_source_id"):
        quote_kind = "UNVERIFIED_PROVIDER_QUOTE"
    refs = []
    flat = {}

    def record(name, value, proof_bars=(), tf="1m", known=None, reason="MEASURED_FROM_CLOSED_OHLC"):
        pb = list(proof_bars)
        state = UNKNOWN if value is None else (TRUE if bool(value) else FALSE)
        end = max((b["close_time"] for b in pb), default=None)
        start = min((b["open_time"] for b in pb), default=None)
        known = end if known is None else known
        if not pb or known is None or end is None or not end <= known <= cutoff or len(dataset_sha256)!=64:
            state, value, known = UNKNOWN, None, None
            reason = "MISSING_CAUSAL_SOURCE" if reason == "MEASURED_FROM_CLOSED_OHLC" else reason
        ref = CausalEvidenceRef(
            evidence_id=canonical_hash({"feature": name, "source": source_hash, "direction": direction}),
            evidence_type=name, symbol=symbol, timeframe=tf.upper(),
            # Exact interval endpoints + count + hash resolve the entire range in the
            # immutable dataset, without duplicating 181 IDs in every feature row.
            source_bar_ids=tuple(dict.fromkeys(f"{symbol}:{tf}:{b['open_time']}" for b in ([pb[0],pb[-1]] if pb else []))),
            source_bar_open_ms=start, source_bar_close_ms=end, occurred_at_ms=start,
            confirmed_at_ms=known, known_at_ms=known, source_dataset_sha256=dataset_sha256,
            source_row_hash=canonical_hash(pb), quote_source_kind=quote_kind,
            measured_value=value, proof_state=state, reason_code=reason, source_bar_count=len(pb))
        refs.append(ref)
        flat[name] = value
        return ref

    desired = "BULLISH" if direction == "LONG" else "BEARISH"
    htf_known = []
    for tf, name in (("4h", "h4_bias"), ("1h", "h1_bias"), ("15m", "m15_bias"), ("5m", "m5_bias")):
        fact = derived["biases"][tf]
        bias = fact["bias"] if fact["status"] == "READY" else None
        ref = record(name, bias, series[tf], tf=tf)
        htf_known.append(bias)
    flat["htf_alignment_state"] = ("UNKNOWN" if any(x is None for x in htf_known[:2]) else
        "ALIGNED" if htf_known[0] == htf_known[1] == desired else
        "COUNTERTREND" if htf_known[0] == htf_known[1] and htf_known[0] in ("BULLISH", "BEARISH") else "MIXED")

    def known_swing(s):
        return s.get("confirmed_at", s.get("known_at_ms"))

    # Existing swing detector includes right-bar confirmation. Never use occurred_at as known_at.
    relevant = sl if direction == "LONG" else sh
    break_refs = sh if direction == "LONG" else sl
    sweep_pair, break_pair = None, None
    for i, b in enumerate(bars):
        for s in relevant:
            k = known_swing(s)
            if k is None or k > b["open_time"]:
                continue
            p = float(s["price"])
            swept = b["low"] < p if direction == "LONG" else b["high"] > p
            if swept:
                reclaim = next((x for x in bars[i:] if (x["close"] > p if direction == "LONG" else x["close"] < p)), None)
                sweep_pair = (s, b, reclaim)
        for s in break_refs:
            k = known_swing(s)
            if k is not None and k <= b["open_time"] and (b["close"] > s["price"] if direction == "LONG" else b["close"] < s["price"]):
                before = [x for x in bars[:i] if x["close_time"] >= k]
                if not any((x["close"] > s["price"] if direction == "LONG" else x["close"] < s["price"]) for x in before):
                    break_pair = (s, b)
    sweep_bars = bars if sweep_pair is None else [x for x in bars if x["close_time"] >= known_swing(sweep_pair[0])]
    record("sweep_confirmed", bool(sweep_pair) if relevant else None, sweep_bars)
    record("sweep_reclaimed", bool(sweep_pair and sweep_pair[2]) if relevant else None, sweep_bars)
    record("mss_confirmed", bool(break_pair) if break_refs else None, bars)
    atr = derived["atr"]
    ratio = None
    if break_pair and atr.status == "AVAILABLE" and atr.value > 0:
        ratio = abs(break_pair[1]["close"] - break_pair[1]["open"]) / atr.value
    flat["displacement_body_atr_ratio"] = ratio
    record("displacement_valid", ratio >= .55 if ratio is not None else None, [break_pair[1]] if break_pair else ())
    record("continuation_retrace_valid", None, reason="RETRACE_CONTRACT_NOT_PROVEN")

    ftype = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
    candidates = [f for f in derived["fvgs"] if f.get("type") == ftype and 0 < int(f.get("confirmed_at", 0)) <= cutoff]
    selected_id = decision.get("selected_fvg_id")
    fvg = next((f for f in reversed(candidates) if not selected_id or f["id"] == selected_id), None)
    record("fvg_present", bool(fvg) if len(bars) >= 3 else None, bars)
    touch, fbars = None, []
    if fvg:
        mid = next((i for i,b in enumerate(bars) if b["open_time"] == fvg["timestamp"]), None)
        if mid is not None and 0 < mid < len(bars)-1:
            fbars = bars[mid-1:mid+2]
            later = [b for b in bars if b["open_time"] >= fvg["confirmed_at"]]
            touch = next((b for b in later if b["high"] >= fvg["bottom"] and b["low"] <= fvg["top"]), None)
    record("fvg_retest_valid", bool(touch) if fbars else None, fbars + ([touch] if touch else []))
    record("fvg_unmitigated", fvg.get("state") in ("confirmed", "partially_mitigated") if fvg else None, bars if fvg else ())
    flat["fvg_age_bars_m1"] = (cutoff-int(fvg["confirmed_at"]))//60_000 if fvg else None
    uni = detect_unicorn_evidence(bars, direction, cutoff, symbol=symbol,
                                 confirmed_swings=(sh, sl)) if len(bars) >= 10 else None
    record("unicorn_breaker_fvg_bound", uni.is_valid if uni else None, bars if uni else ())
    flat["unicorn_classification"] = "UNICORN_CONFIRMED" if uni and uni.is_valid else "FVG_ONLY" if fvg else "UNKNOWN"

    entry, stop, target = decision.get("entry_reference"), decision.get("selected_sl"), decision.get("take_profit")
    anchor = None
    if entry is not None and stop is not None:
        good = [s for s in relevant if known_swing(s) is not None and known_swing(s) <= cutoff and
                (float(stop) < s["price"] < float(entry) if direction == "LONG" else float(stop) > s["price"] > float(entry))]
        requested = decision.get("selected_anchor_id")
        anchor = next((s for s in reversed(good) if requested and s.get("id") == requested), None)
    record("protected_structure_independent_of_sl", True if anchor else None, bars if anchor else (), reason="ANCHOR_ID_BOUND_TO_CONFIRMED_SWING" if anchor else "NO_VERIFIED_ANCHOR_ID")
    record("target_reachable", None, reason="NO_PRESPECIFIED_REACHABILITY_MODEL")
    flat["target_room_atr_m1"] = abs(float(target)-float(entry))/atr.value if target is not None and entry is not None and atr.status == "AVAILABLE" and atr.value>0 else None
    target_id = decision.get("selected_target_id")
    pools = [s for s in break_refs if target_id and s.get("id") == target_id and known_swing(s) is not None and known_swing(s)<=cutoff]
    pool = next((s for s in pools if target is not None and abs(float(target)-float(s["price"])) <= .011), None)
    swept, target_sweep_bar = None, None
    if pool:
        later = [b for b in bars if b["open_time"] >= known_swing(pool)]
        target_sweep_bar = next((b for b in later if (b["high"] >= pool["price"] if direction == "LONG" else b["low"] <= pool["price"])),None)
        swept = target_sweep_bar is not None
    record("target_unswept", not swept if swept is not None else None, bars if pool else (), reason="TARGET_POOL_AND_POST_CONFIRMATION_PATH" if pool else "NO_VERIFIED_TARGET_POOL")
    flat["target_pool_state"] = "ALREADY_SWEPT" if swept is True else "UNSWEPT" if swept is False else "UNKNOWN"
    flat["target_pool_id"] = pool.get("id") if pool else None
    flat["target_sweep_bar_id"] = f"{symbol}:1m:{target_sweep_bar['open_time']}" if target_sweep_bar else None
    bid, ask = _get(context,"bid"), _get(context,"ask")
    flat["quote_source_kind"] = quote_kind
    flat["spread_to_atr_m1_ratio"] = (float(ask)-float(bid))/atr.value if bid is not None and ask is not None and atr.status=="AVAILABLE" and atr.value>0 else None
    oid = str(decision.get("opportunity_id", ""))
    ep = str(decision.get("setup_episode_id", decision.get("setup_id", "")))
    snapshot = VerifiedPreEntryFeatureSnapshot(oid, ep, cutoff, source_hash, tuple(refs), tuple(sorted(flat.items())), "")
    payload = asdict(snapshot)
    payload.pop("proof_hash")
    from dataclasses import replace
    snapshot = replace(snapshot, proof_hash=canonical_hash(payload))
    return {**flat, "opportunity_id":oid,"decision_timestamp_ms":cutoff,
            "model_id":decision.get("model_id"),"direction":direction,
            "causal_snapshot":snapshot.to_dict(),"proof_hash":snapshot.proof_hash,
            "source_snapshot_hash":source_hash,"source_dataset_sha256":dataset_sha256,
            "evidence_derivation_version":VERSION}


def build_verified_baseline_pre_entry_feature_ledger_v1793127(candidate_index, datasets, *, dataset_sha256):
    """Cover every baseline opportunity; no sample-record or optimistic fallback."""
    from types import SimpleNamespace
    # The canonical loader names hourly frames 1H/4H; contracts use 1h/4h.
    datasets = {str(k).lower(): v for k,v in datasets.items()}
    fields = {"1m":"closed_m1_candles","5m":"closed_m5_candles","15m":"closed_m15_candles",
              "1h":"closed_h1_candles","4h":"closed_h4_candles"}
    times = {tf:[int(_get(b,"close_time",_get(b,"timestamp",0)+TF[tf])) for b in datasets.get(tf,())] for tf in fields}
    if any(ts != sorted(set(ts)) for ts in times.values()):
        raise ValueError("NON_MONOTONIC_OR_DUPLICATE_DATASET_BARS")
    ledger = {}
    for idx, records in sorted(candidate_index["decisions_by_checkpoint_index"].items()):
        if len(ledger)%128==0:
            import shutil
            import psutil
            if psutil.virtual_memory().available < 1024**3:
                raise RuntimeError("BLOCKED_LOW_RAM_CAUSAL_LEDGER")
            if shutil.disk_usage(Path(__file__).anchor).free < 5*1024**3:
                raise RuntimeError("BLOCKED_LOW_DISK_CAUSAL_LEDGER")
        for rec in records:
            cutoff = rec.get("decision_timestamp_ms")
            if not isinstance(cutoff,int) or cutoff<=0:
                raise ValueError("MISSING_DECISION_TIMESTAMP")
            slices={}
            for tf, field in fields.items():
                end=bisect.bisect_right(times[tf],cutoff)
                size=181 if tf=="1m" else 80
                slices[field]=datasets.get(tf,())[max(0,end-size):end]
            px=datasets["1m"][int(idx)]["close"]
            context=SimpleNamespace(symbol="XAUUSDT",decision_timestamp_ms=cutoff,**slices,
                                    bid=float(px)-.10,ask=float(px)+.10,quote_source_kind="SYNTHETIC_RESEARCH_QUOTE")
            oid=str(rec["opportunity_id"])
            if oid in ledger:
                raise ValueError("DUPLICATE_OPPORTUNITY_ID")
            ledger[oid]=derive_pre_entry_features_from_causal_context_v1793127(rec,context,dataset_sha256=dataset_sha256)
    return ledger


def required_proof_rejection_v1793127(feature, model_id):
    requires_unicorn = model_id == "UNICORN_MODEL"
    model_id={"ICT_2022_MODEL":"SMC_M1_CONTINUATION","UNICORN_MODEL":"SMC_M1_FVG_CONTINUATION",
              "SILVER_BULLET":"SMC_M1_SWEEP_RECLAIM"}.get(model_id,model_id)
    if model_id not in ("SMC_M1_CONTINUATION","SMC_M1_REVERSAL","SMC_M1_FVG_CONTINUATION","SMC_M1_SWEEP_RECLAIM","CG01_SWEEP_MSS_FVG"):
        return "REJECTED_UNPROVEN:UNSUPPORTED_MODEL"
    if not verify_feature_snapshot_v1793127(feature):
        return "REJECTED_UNPROVEN:INVALID_OR_MISSING_PROOF_HASH"
    if feature.get("htf_alignment_state") != "ALIGNED":
        return "REJECTED_UNPROVEN:HTF_NOT_ALIGNED"
    required=["protected_structure_independent_of_sl","target_unswept"]
    if requires_unicorn:
        required += ["unicorn_breaker_fvg_bound"]
    if model_id in ("SMC_M1_REVERSAL","SMC_M1_SWEEP_RECLAIM","CG01_SWEEP_MSS_FVG"):
        required += ["sweep_confirmed","sweep_reclaimed","mss_confirmed"]
    if model_id in ("UNICORN_MODEL","SMC_M1_FVG_CONTINUATION","CG01_SWEEP_MSS_FVG"):
        required += ["fvg_present","fvg_retest_valid","fvg_unmitigated"]
    if model_id=="SMC_M1_CONTINUATION":
        required += ["mss_confirmed","displacement_valid"]
    refs={r["evidence_type"]:r for r in (feature.get("causal_snapshot") or {}).get("features",())}
    for key in required:
        if key not in refs or refs[key].get("proof_state")!=TRUE or feature.get(key) is not True:
            return "REJECTED_UNPROVEN:"+key
    return None


def verify_feature_snapshot_v1793127(row):
    snap = row.get("causal_snapshot")
    if not isinstance(snap, Mapping):
        return False
    payload = dict(snap)
    digest = payload.pop("proof_hash", None)
    if not digest or canonical_hash(payload) != digest or row.get("proof_hash") != digest:
        return False
    if snap.get("opportunity_id") != row.get("opportunity_id") or snap.get("decision_timestamp_ms") != row.get("decision_timestamp_ms"):
        return False
    if snap.get("source_snapshot_hash") != row.get("source_snapshot_hash"):
        return False
    if any(row.get(k) != v for k,v in snap.get("derived_values", ())):
        return False
    cutoff = snap["decision_timestamp_ms"]
    for ref in snap.get("features", ()):
        if ref.get("proof_state") not in (TRUE,FALSE,UNKNOWN):
            return False
        if ref["proof_state"] != UNKNOWN:
            times=[ref.get(k) for k in ("source_bar_close_ms","confirmed_at_ms","known_at_ms")]
            if any(isinstance(t,bool) or not isinstance(t,int) or t<=0 for t in times) or not times[0]<=times[1]<=times[2]<=cutoff:
                return False
            if not ref.get("source_bar_ids") or len(ref.get("source_row_hash", ""))!=64:
                return False
    return True


def classify_target_failure_modes_v1793127(feature):
    if not verify_feature_snapshot_v1793127(feature):
        return {"label":"UNKNOWN","confidence":"UNKNOWN","evidence_refs":[]}
    refs={r["evidence_type"]:r for r in feature["causal_snapshot"]["features"]}
    ref=refs.get("target_unswept",{})
    if ref.get("proof_state")==FALSE and feature.get("target_pool_id") and feature.get("target_sweep_bar_id"):
        return {"label":"TARGET_ALREADY_SWEPT","confidence":"OBSERVED","evidence_refs":[ref["evidence_id"]]}
    return {"label":"UNKNOWN","confidence":"UNKNOWN","evidence_refs":[]}


def classify_observed_loss_v1793127(ep,feature):
    labels=[]
    if ep.get("outcome_label")=="DATA_GAP_UNRESOLVED" or ep.get("ambiguous_same_bar") is True:
        labels.append("FILL_GAP/AMBIGUOUS_INTRABAR")
    gross,net=ep.get("realized_gross_pnl_usd"),ep.get("realized_net_pnl_usd")
    if gross is not None and net is not None and float(gross)>=0 and float(net)<0:
        fees=float(ep.get("entry_fee_usd") or 0)+float(ep.get("exit_fee_usd") or 0)
        if abs(float(gross)-fees-float(net))<=.001:
            labels.append("FEES_DOMINATE")
    if verify_feature_snapshot_v1793127(feature):
        refs={r["evidence_type"]:r for r in feature["causal_snapshot"]["features"]}
        if feature.get("htf_alignment_state")=="COUNTERTREND" and all(refs.get(k,{}).get("proof_state")!=UNKNOWN for k in ("h4_bias","h1_bias")):
            labels.append("WRONG_SIDE_HTF")
        if refs.get("sweep_confirmed",{}).get("proof_state")==TRUE and refs.get("sweep_reclaimed",{}).get("proof_state")==FALSE:
            labels.append("SWEEP_NOT_RECLAIMED")
        target=classify_target_failure_modes_v1793127(feature)
        if target["label"]!="UNKNOWN":
            labels.append(target["label"])
    return (labels[0],labels) if labels else ("OTHER/UNKNOWN",["OTHER/UNKNOWN"])


def build_forensic_causal_evidence_join_v1793127(episodes,ledger):
    output=[]
    seen=set()
    for ep in episodes:
        oid=ep.get("opportunity_id")
        if not oid or oid in seen:
            raise ValueError("DUPLICATE_OR_MISSING_EPISODE_OPPORTUNITY")
        seen.add(oid)
        feature=ledger.get(oid,{})
        primary,labels=classify_observed_loss_v1793127(ep,feature)
        output.append({"opportunity_id":oid,"observed_execution":dict(ep),
                       "pre_entry_snapshot":feature.get("causal_snapshot"),
                       "primary_label":primary,"observation_tags":labels,
                       "inferred_hypothesis":"UNKNOWN","counterfactual_assessment_status":"NOT_ESTABLISHED",
                       "confidence":"UNKNOWN" if primary=="OTHER/UNKNOWN" else "OBSERVED_NOT_CAUSAL",
                       "source_proof_valid":verify_feature_snapshot_v1793127(feature)})
    return output
