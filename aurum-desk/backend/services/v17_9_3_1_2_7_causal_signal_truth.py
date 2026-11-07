"""Point-in-time research evidence. No orders, wall clock, or outcome inputs.

This module composes the existing structure/FVG/Unicorn detectors; it is not
a second strategy evaluator. Missing proof stays UNKNOWN in persisted output.
"""
from __future__ import annotations

import bisect
from collections import OrderedDict, Counter, ChainMap
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import zlib
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from services.research_resource_guard import raise_if_research_aborted

VERSION = "17.9.3.1.2.7"
TYPED_EVIDENCE_VERSION = "17.9.3.1.2.8"
TF = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
      "1h": 3_600_000, "4h": 14_400_000}
TRUE, FALSE, UNKNOWN = "PROVEN_TRUE", "PROVEN_FALSE", "UNKNOWN"
MEASURED = "MEASURED_VALUE"
GEOMETRY_FIELDS=("model_id","direction","entry_reference","selected_sl","take_profit",
                 "hypothetical_quantity","selected_anchor_id","selected_target_id","structural_invalidation_price",
                 "selected_fvg_id","selected_breaker_id","selected_sweep_id","selected_break_id","setup_episode_id")
SOURCE_IMPLEMENTATION_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class CompressedFeatureLedger(MutableMapping):
    """Bounded-memory pure input cache; each read returns an independent record."""
    def __init__(self):
        self._encoded={}
        self.proof_hashes={}
        self._datasets={}
        self._dataset_counts=Counter()
    def __getitem__(self,key):
        return json.loads(zlib.decompress(self._encoded[key]))
    def __setitem__(self,key,value):
        if key in self._encoded:self.__delitem__(key)
        self._encoded[key]=zlib.compress(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode(),1)
        self.proof_hashes[key]=value.get("proof_hash")
        dataset=value.get("source_dataset_sha256")
        self._datasets[key]=dataset
        self._dataset_counts[dataset]+=1
    def __delitem__(self,key):
        del self._encoded[key]
        del self.proof_hashes[key]
        dataset=self._datasets.pop(key)
        self._dataset_counts[dataset]-=1
        if not self._dataset_counts[dataset]:del self._dataset_counts[dataset]
    def __iter__(self):return iter(self._encoded)
    def __len__(self):return len(self._encoded)
    def __contains__(self,key):return key in self._encoded
    @property
    def dataset_hashes(self):return {k for k in self._dataset_counts if k}
    @property
    def encoded_bytes(self):return sum(len(v) for v in self._encoded.values())


def ledger_dataset_hashes(ledger):
    if isinstance(ledger,CompressedFeatureLedger):return ledger.dataset_hashes
    if isinstance(ledger,ChainMap):return set().union(*(ledger_dataset_hashes(m) for m in ledger.maps))
    return {r.get("source_dataset_sha256") for r in ledger.values() if r.get("source_dataset_sha256")}


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
        if type(op) is not int or op <= 0:
            continue
        # Deriving a bar's mathematical close is not inventing a signal known_at.
        cl = int(op) + duration if cl is None else cl
        if type(cl) is not int:
            continue
        if cl != op + duration or cl > cutoff or _get(b, "is_closed") is not True:
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
        if not _valid_evidence_state(self.proof_state, self.measured_value, self.evidence_derivation_version):
            raise ValueError("INVALID_PROOF_STATE")
        if self.proof_state != UNKNOWN:
            times = (self.source_bar_close_ms, self.confirmed_at_ms, self.known_at_ms)
            if any(isinstance(t, bool) or not isinstance(t, int) or t <= 0 for t in times):
                raise ValueError("MISSING_CAUSAL_TIMESTAMP")
            if not times[0] <= times[1] <= times[2] or not self.source_bar_ids:
                raise ValueError("IMPOSSIBLE_CAUSAL_REFERENCE")


def _valid_evidence_state(state, value, version):
    if version == VERSION:
        return state in (TRUE, FALSE, UNKNOWN)
    if version != TYPED_EVIDENCE_VERSION:
        return False
    if state == UNKNOWN:
        return value is None
    if state == MEASURED:
        return isinstance(value, str) and bool(value)
    return (state == TRUE and value is True) or (state == FALSE and value is False)


def _closed_price_path(by_open, start, cutoff):
    """Cover the interval exactly with closed source bars, coarse first.

    A coarse OHLC bar can prove a level was touched, but not its intrabar
    sequence. Never jump across an uncovered period or use a partial bar.
    """
    path = []
    cursor = start
    while cursor < cutoff:
        for timeframe in reversed(TF):
            bar = by_open[timeframe].get(cursor)
            if bar is not None and bar["close_time"] <= cutoff:
                path.append((timeframe, bar))
                cursor = bar["close_time"]
                break
        else:
            break
    return path, cursor == cutoff


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
        # The structural selectors use 1/1 pivots for higher-frame source IDs.
        selector_swings = {tf: identify_confirmed_swings(series[tf], timeframe=tf.upper(),
                                                         left_bars=1, right_bars=1, as_of_ms=cutoff)
                           for tf in ("5m", "15m", "1h", "4h")}
        selector_swings["1m"] = swings["1m"]
        derived = {"swings": swings, "biases": biases, "atr": atr, "fvgs": fvgs,
                   "selector_swings": selector_swings,
                   "bars_by_open": {tf: {bar["open_time"]: bar for bar in rows} for tf, rows in series.items()}}
        _CACHE[cache_key] = derived
        if len(_CACHE) > 64:
            _CACHE.popitem(last=False)
    bars = series["1m"]
    sh, sl = derived["swings"]["1m"]
    quote_kind = str(_get(context, "quote_source_kind", "UNKNOWN"))
    if quote_kind == "REAL_PROVIDER_QUOTE":
        quote_ts=_get(context,"quote_timestamp_ms")
        if not _get(context,"quote_provider_source_id") or isinstance(quote_ts,bool) or not isinstance(quote_ts,int) or not 0<quote_ts<=cutoff:
            quote_kind = "UNVERIFIED_PROVIDER_QUOTE"
    refs = []
    flat = {}

    def record(name, value, proof_bars=(), tf="1m", known=None, occurred=None,
               reason="MEASURED_FROM_CLOSED_OHLC", source_path=()):
        pb = list(proof_bars)
        state = UNKNOWN if value is None else TRUE if value is True else FALSE if value is False else MEASURED
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
            source_bar_ids=(tuple(f"{symbol}:{frame}:{bar['open_time']}" for frame, bar in source_path)
                            if source_path else tuple(dict.fromkeys(f"{symbol}:{tf}:{b['open_time']}" for b in ([pb[0],pb[-1]] if pb else [])))),
            # Window start is source coverage, not a market event timestamp.
            # Measurement availability comes from the last closed source bar;
            # an event occurrence stays absent unless a detector actually found it.
            source_bar_open_ms=start, source_bar_close_ms=end, occurred_at_ms=occurred,
            confirmed_at_ms=known, known_at_ms=known, source_dataset_sha256=dataset_sha256,
            source_row_hash=canonical_hash(source_path or pb), quote_source_kind=quote_kind,
            measured_value=value, proof_state=state, reason_code=reason, source_bar_count=len(pb),
            evidence_derivation_version=TYPED_EVIDENCE_VERSION)
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
    sweep_matches, break_matches = [], []
    model = decision.get("model_id")
    sweep_id = decision.get("selected_sweep_id")
    break_id = decision.get("selected_break_id")
    for i, b in enumerate(bars):
        opened = b["open_time"]
        sweep_ids = {f"sweep_{model}_{opened}", f"sweep_si8_{model}_{opened}"}
        if model == "CG01_SWEEP_MSS_FVG":
            sweep_ids.add(f"sweep_cg01_{opened}")
        break_ids = {f"brk_{direction}_{opened}", f"bos_{direction}_{opened}",
                     f"bos_si8_{direction}_{opened}", f"mss_si8_{model}_{direction}_{opened}"}
        for s in relevant if sweep_id in sweep_ids else ():
            k = known_swing(s)
            if k is None or k > b["open_time"]:
                continue
            p = float(s["price"])
            swept = b["low"] < p if direction == "LONG" else b["high"] > p
            if swept:
                reclaim = next((x for x in bars[i:] if (x["close"] > p if direction == "LONG" else x["close"] < p)), None)
                sweep_matches.append((s, b, reclaim))
        for s in break_refs if break_id in break_ids else ():
            k = known_swing(s)
            if k is not None and k <= b["open_time"] and (b["close"] > s["price"] if direction == "LONG" else b["close"] < s["price"]):
                before = [x for x in bars[:i] if x["close_time"] >= k]
                if not any((x["close"] > s["price"] if direction == "LONG" else x["close"] < s["price"]) for x in before):
                    break_matches.append((s, b))
    sweep_pair = sweep_matches[0] if len(sweep_matches) == 1 else None
    break_pair = break_matches[0] if len(break_matches) == 1 else None
    sweep_bars = bars if sweep_pair is None else [x for x in bars if x["close_time"] >= known_swing(sweep_pair[0])]
    record("sweep_confirmed", True if sweep_pair else None, sweep_bars, occurred=sweep_pair[1]["open_time"] if sweep_pair else None,
           reason="AMBIGUOUS_SOURCE" if len(sweep_matches) > 1 else "SELECTED_SWEEP_SOURCE")
    record("sweep_reclaimed", bool(sweep_pair[2]) if sweep_pair else None, sweep_bars, occurred=sweep_pair[2]["open_time"] if sweep_pair and sweep_pair[2] else None)
    record("mss_confirmed", True if break_pair else None, bars, occurred=break_pair[1]["open_time"] if break_pair else None,
           reason="AMBIGUOUS_SOURCE" if len(break_matches) > 1 else "SELECTED_BREAK_SOURCE")
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
    record("fvg_present", bool(fvg) if len(bars) >= 3 else None, bars, occurred=int(fvg["timestamp"]) if fvg else None)
    touch, fbars = None, []
    if fvg:
        mid = next((i for i,b in enumerate(bars) if b["open_time"] == fvg["timestamp"]), None)
        if mid is not None and 0 < mid < len(bars)-1:
            fbars = bars[mid-1:mid+2]
            later = [b for b in bars if b["open_time"] >= fvg["confirmed_at"]]
            touch = next((b for b in later if b["high"] >= fvg["bottom"] and b["low"] <= fvg["top"]), None)
    record("fvg_retest_valid", bool(touch) if fbars else None, fbars + ([touch] if touch else []), occurred=touch["open_time"] if touch else None)
    record("fvg_unmitigated", fvg.get("state") in ("confirmed", "partially_mitigated") if fvg else None, bars if fvg else ())
    flat["fvg_age_bars_m1"] = (cutoff-int(fvg["confirmed_at"]))//60_000 if fvg else None
    uni = detect_unicorn_evidence(bars, direction, cutoff, symbol=symbol,
                                 confirmed_swings=(sh, sl)) if len(bars) >= 10 else None
    bound_uni=bool(uni and uni.is_valid and fvg and _get(uni,"selected_fvg_id",_get(uni,"fvg_id"))==fvg["id"]
                   and (not decision.get("selected_breaker_id") or decision["selected_breaker_id"]==_get(uni,"breaker_id")))
    record("unicorn_breaker_fvg_bound", bound_uni if uni else None, bars if uni else ())
    flat["unicorn_classification"] = "UNICORN_CONFIRMED" if bound_uni else "FVG_ONLY" if fvg else "UNKNOWN"
    record("sweep_mss_ordered", bool(sweep_pair[2] and break_pair[1]["open_time"]>=sweep_pair[2]["close_time"]) if sweep_pair and break_pair else None,bars)
    record("mss_fvg_retest_ordered", bool(int(fvg["confirmed_at"])>=break_pair[1]["close_time"] and touch["open_time"]>=int(fvg["confirmed_at"])) if fvg and break_pair and touch else None,bars)
    flat["sweep_source_pivot_id"]=sweep_pair[0].get("id") if sweep_pair else None
    flat["mss_source_pivot_id"]=break_pair[0].get("id") if break_pair else None
    flat["fvg_source_id"]=fvg.get("id") if fvg else None
    flat["unicorn_source_breaker_id"]=_get(uni,"breaker_id") if bound_uni else None

    entry, stop, target = decision.get("entry_reference"), decision.get("selected_sl"), decision.get("take_profit")
    anchor = None
    anchor_bars = bars
    anchor_tf = "1m"
    if entry is not None and stop is not None:
        good = [s for s in relevant if known_swing(s) is not None and known_swing(s) <= cutoff and
                (float(stop) < s["price"] < float(entry) if direction == "LONG" else float(stop) > s["price"] > float(entry))]
        requested = decision.get("selected_anchor_id")
        anchor = next((s for s in reversed(good) if requested and s.get("id") == requested), None)
        if isinstance(requested, str) and requested.startswith("v1792_m5_"):
            highs, lows = derived["selector_swings"]["5m"]
            selected = [s for s in (lows if direction == "LONG" else highs)
                        if requested == f'v1792_m5_{s["id"]}']
            if len(selected) == 1:
                swing = selected[0]
                price = swing["price"]
                protective = float(stop) < price < float(entry) if direction == "LONG" else float(stop) > price > float(entry)
                occurred = swing["occurred_at_ms"]
                known = known_swing(swing)
                window = [b for b in series["5m"] if occurred - TF["5m"] <= b["open_time"] and b["close_time"] <= known]
                expected = [occurred - TF["5m"], occurred, occurred + TF["5m"]]
                if (protective and decision.get("structural_invalidation_price") == price
                        and [b["open_time"] for b in window] == expected and window[-1]["close_time"] == known):
                    anchor, anchor_bars, anchor_tf = swing, window, "5m"
    record("protected_structure_independent_of_sl", True if anchor else None, anchor_bars if anchor else (),
           tf=anchor_tf, known=known_swing(anchor) if anchor_tf == "5m" else None,
           occurred=anchor.get("occurred_at_ms") if anchor else None,
           reason="ANCHOR_ID_BOUND_TO_CONFIRMED_SWING" if anchor else "NO_VERIFIED_ANCHOR_ID")
    record("target_reachable", None, reason="NO_PRESPECIFIED_REACHABILITY_MODEL")
    flat["target_room_atr_m1"] = abs(float(target)-float(entry))/atr.value if target is not None and entry is not None and atr.status == "AVAILABLE" and atr.value>0 else None
    target_id = decision.get("selected_target_id")
    pools = []
    if target_id:
        for tf, (highs, lows) in derived["selector_swings"].items():
            for swing in (highs if direction == "LONG" else lows):
                if known_swing(swing) is not None and known_swing(swing) <= cutoff:
                    if target_id == swing.get("id") or (isinstance(target_id, str) and target_id == f'pool_{tf.upper()}_{swing.get("id")}'):
                        pools.append((tf, swing))
    pool = None
    target_source_bars = []
    target_tf = "1m"
    if len(pools) == 1 and entry is not None and target is not None:
        target_tf, candidate = pools[0]
        price = candidate["price"]
        # Upstream pivot pools use the actual selected price. Exact equality is
        # sufficient without a broker tick contract and cannot bind a nearby pool.
        if float(target) == price and (price > float(entry) if direction == "LONG" else price < float(entry)):
            pivot_open = candidate["occurred_at_ms"]
            duration = TF[target_tf]
            width = 2 if target_tf == "1m" else 1
            target_source_bars = [b for b in series[target_tf]
                                  if pivot_open - width * duration <= b["open_time"]
                                  and b["close_time"] <= known_swing(candidate)]
            if [b["open_time"] for b in target_source_bars] == [pivot_open + i * duration for i in range(-width, width + 1)]:
                pool = candidate
    swept, target_sweep = None, None
    path = []
    if pool:
        start = pool["occurred_at_ms"] + TF[target_tf]
        path, complete = _closed_price_path(derived["bars_by_open"], start, cutoff)
        def touched(bar):
            return bar["high"] >= pool["price"] if direction == "LONG" else bar["low"] <= pool["price"]

        # Finer observations may expose a touch missed by inconsistent aggregate
        # bars. An observed touch disproves unswept even if another interval is missing.
        touches = [(tf, b) for tf, source in series.items() for b in source
                   if b["open_time"] >= start and touched(b)]
        if touches:
            target_sweep = min(touches, key=lambda item: (item[1]["close_time"], TF[item[0]]))
            path = [target_sweep]
        swept = True if target_sweep else False if path and complete else None
    record("target_selected_pool_bound", True if pool else None, target_source_bars if pool else (),
           tf=target_tf, known=known_swing(pool) if pool else None,
           occurred=pool["occurred_at_ms"] if pool else None)
    record("target_unswept", not swept if swept is not None else None,
           [bar for _, bar in path], tf="MULTI", source_path=path,
           reason="TARGET_POOL_AND_OBSERVED_PATH" if swept is not None else
           "INCOMPLETE_POST_PIVOT_PATH" if pool else "NO_VERIFIED_TARGET_POOL")
    flat["target_pool_state"] = "ALREADY_SWEPT" if swept is True else "UNSWEPT" if swept is False else "UNKNOWN"
    flat["target_pool_id"] = target_id if pool else None
    flat["target_sweep_bar_id"] = f"{symbol}:{target_sweep[0]}:{target_sweep[1]['open_time']}" if target_sweep else None
    bid, ask = _get(context,"bid"), _get(context,"ask")
    flat["quote_source_kind"] = quote_kind
    flat["decision_geometry"]={k:decision.get(k) for k in GEOMETRY_FIELDS}
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
            "evidence_derivation_version":TYPED_EVIDENCE_VERSION}


def build_verified_baseline_pre_entry_feature_ledger_v1793127(candidate_index, datasets, *, dataset_sha256, compact=False, proof_hashes_only=False):
    """Cover every baseline opportunity; no sample-record or optimistic fallback."""
    from types import SimpleNamespace
    # The canonical loader names hourly frames 1H/4H; contracts use 1h/4h.
    datasets = {str(k).lower(): v for k,v in datasets.items()}
    fields = {"1m":"closed_m1_candles","5m":"closed_m5_candles","15m":"closed_m15_candles",
              "1h":"closed_h1_candles","4h":"closed_h4_candles"}
    times = {tf:[int(_get(b,"close_time",_get(b,"timestamp",0)+TF[tf])) for b in datasets.get(tf,())] for tf in fields}
    if any(ts != sorted(set(ts)) for ts in times.values()):
        raise ValueError("NON_MONOTONIC_OR_DUPLICATE_DATASET_BARS")
    # Auditors need only the hashes of independently derived complete snapshots.
    ledger = {} if proof_hashes_only else CompressedFeatureLedger() if compact else {}
    for idx, records in sorted(candidate_index["decisions_by_checkpoint_index"].items()):
        raise_if_research_aborted()
        if len(ledger)%128==0:
            import os
            import shutil
            import psutil
            min_ram = int(os.environ.get("AURUM_MINIMUM_RAM_BYTES", 512 * 1024**2))
            if psutil.virtual_memory().available < min_ram:
                raise RuntimeError("BLOCKED_LOW_RAM_CAUSAL_LEDGER")
            if shutil.disk_usage(Path(__file__).anchor).free < 5*1024**3:
                raise RuntimeError("BLOCKED_LOW_DISK_CAUSAL_LEDGER")
        if len(ledger) > 0 and len(ledger) % 2048 == 0:
            import gc
            gc.collect()
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
            feature=derive_pre_entry_features_from_causal_context_v1793127(rec,context,dataset_sha256=dataset_sha256)
            ledger[oid]=feature["proof_hash"] if proof_hashes_only else feature
    return ledger


def required_proof_rejection_v1793127(feature, model_id, *, decision=None):
    requires_unicorn = model_id == "UNICORN_MODEL"
    model_id={"ICT_2022_MODEL":"SMC_M1_CONTINUATION","UNICORN_MODEL":"SMC_M1_FVG_CONTINUATION",
              "SILVER_BULLET":"SMC_M1_SWEEP_RECLAIM"}.get(model_id,model_id)
    if model_id not in ("SMC_M1_CONTINUATION","SMC_M1_REVERSAL","SMC_M1_FVG_CONTINUATION","SMC_M1_SWEEP_RECLAIM","CG01_SWEEP_MSS_FVG"):
        return "REJECTED_UNPROVEN:UNSUPPORTED_MODEL"
    if not verify_feature_snapshot_v1793127(feature):
        return "REJECTED_UNPROVEN:INVALID_OR_MISSING_PROOF_HASH"
    if decision is not None and feature.get("decision_geometry")!={k:decision.get(k) for k in GEOMETRY_FIELDS}:
        return "REJECTED_UNPROVEN:INTENT_GEOMETRY_MISMATCH"
    if decision is not None and decision.get("decision_timestamp_ms")!=feature.get("decision_timestamp_ms"):
        return "REJECTED_UNPROVEN:INTENT_TIMESTAMP_MISMATCH"
    if feature.get("htf_alignment_state") != "ALIGNED":
        return "REJECTED_UNPROVEN:HTF_NOT_ALIGNED"
    required=["protected_structure_independent_of_sl","target_unswept"]
    if requires_unicorn:
        required += ["unicorn_breaker_fvg_bound"]
    if model_id in ("SMC_M1_REVERSAL","SMC_M1_SWEEP_RECLAIM","CG01_SWEEP_MSS_FVG"):
        required += ["sweep_confirmed","sweep_reclaimed","mss_confirmed","sweep_mss_ordered"]
    if model_id in ("UNICORN_MODEL","SMC_M1_FVG_CONTINUATION","CG01_SWEEP_MSS_FVG"):
        if not feature.get("decision_geometry", {}).get("selected_fvg_id"):
            return "REJECTED_UNPROVEN:MISSING_SELECTED_FVG_ID"
        required += ["fvg_present","fvg_retest_valid","fvg_unmitigated","mss_confirmed","displacement_valid","mss_fvg_retest_ordered"]
    if model_id=="CG01_SWEEP_MSS_FVG":required += ["unicorn_breaker_fvg_bound"]
    if model_id=="SMC_M1_CONTINUATION":
        required += ["mss_confirmed","displacement_valid"]
    refs={r["evidence_type"]:r for r in (feature.get("causal_snapshot") or {}).get("features",())}
    for key in required:
        if key not in refs or refs[key].get("proof_state")!=TRUE or feature.get(key) is not True:
            return "REJECTED_UNPROVEN:"+key
    return None


def rebuild_materialized_intent_proof_v1793127(opportunity,plan,datasets,*,dataset_sha256):
    """Recheck actual final geometry at its own closed-bar decision time."""
    record={**opportunity,"decision_timestamp_ms":plan["new_decision_timestamp_ms"],
            "checkpoint_index_1m":plan["new_checkpoint_index_1m"]}
    for field,new_field in (("entry_reference","new_entry_reference"),("selected_sl","new_selected_sl"),
                            ("take_profit","new_take_profit"),("hypothetical_quantity","new_quantity"),
                            ("selected_anchor_id","new_selected_anchor_id"),("selected_target_id","new_selected_target_id"),
                            ("structural_invalidation_price","new_structural_invalidation_price")):
        record[field]=plan.get(new_field,opportunity.get(field))
    index=int(record["checkpoint_index_1m"])
    if index<0 or index>=len(datasets["1m"]) or int(datasets["1m"][index]["close_time"])!=record["decision_timestamp_ms"]:
        raise ValueError("FINAL_INTENT_NOT_AT_CLOSED_SOURCE_BAR")
    proof=build_verified_baseline_pre_entry_feature_ledger_v1793127(
        {"decisions_by_checkpoint_index":{index:[record]}},datasets,dataset_sha256=dataset_sha256)[str(record["opportunity_id"])]
    rejection = required_proof_rejection_v1793127(proof, str(record["model_id"]), decision=record)
    if rejection is None:
        from services.v17_9_3_1_2_executable_intervention_replay_service import recalculate_trade_risk_quantity_v179312

        prices_and_size = [record.get(key) for key in ("entry_reference", "selected_sl", "take_profit", "hypothetical_quantity")]
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in prices_and_size):
            rejection = "REJECTED_UNPROVEN:INVALID_FINAL_PRICE_OR_SIZE"
        elif record["decision_timestamp_ms"] < opportunity["decision_timestamp_ms"]:
            rejection = "REJECTED_UNPROVEN:FINAL_INTENT_BACKDATED"
        else:
            entry, stop, target, quantity = prices_and_size
            risk = recalculate_trade_risk_quantity_v179312(entry, stop, target, direction=record["direction"],
                                                           decision_ts=record["decision_timestamp_ms"])
            if (not risk["can_execute"] or risk["planned_net_risk_usd"] > 2.50 or risk["net_rr"] < 2.0
                    or quantity != risk["quantity"]):
                rejection = "REJECTED_UNPROVEN:FINAL_RISK_OR_QUANTITY_MISMATCH"
    return record,proof,rejection


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
    geometry=row.get("decision_geometry") or {}
    if geometry.get("model_id")!=row.get("model_id") or str(geometry.get("direction","")).upper()!=str(row.get("direction","")).upper():
        return False
    dataset_sha=row.get("source_dataset_sha256","")
    if not isinstance(dataset_sha,str) or len(dataset_sha)!=64:
        return False
    cutoff = snap["decision_timestamp_ms"]
    for ref in snap.get("features", ()):
        if ref.get("source_dataset_sha256")!=dataset_sha:return False
        if ref.get("evidence_derivation_version") != row.get("evidence_derivation_version"):
            return False
        if not _valid_evidence_state(ref.get("proof_state"), ref.get("measured_value"), ref.get("evidence_derivation_version")):
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
    canonical_seen=set()
    dataset_hashes=ledger_dataset_hashes(ledger)
    for ep in episodes:
        oid=ep.get("opportunity_id")
        if not oid or oid in seen:
            raise ValueError("DUPLICATE_OR_MISSING_EPISODE_OPPORTUNITY")
        seen.add(oid)
        feature=ledger.get(oid)
        canonical_id=oid
        if feature is None and dataset_hashes:
            if len(dataset_hashes)!=1:raise ValueError("AMBIGUOUS_FORENSIC_DATASET_AUTHORITY")
            from services.v17_9_3_1_2_1_root_cause_evidence_service import build_canonical_opportunity_id_v1793121
            try:
                source_record={**ep,"model_id":ep.get("source_model_id",ep.get("model_id"))}
                candidate_id=build_canonical_opportunity_id_v1793121(source_record,dataset_sha256=next(iter(dataset_hashes)))
                feature=ledger.get(candidate_id)
                if feature is not None:canonical_id=candidate_id
            except (ValueError,TypeError):pass
        feature=feature or {}
        final_feature=ep.get("v1793127_final_feature_proof")
        if final_feature is not None:
            if not verify_feature_snapshot_v1793127(final_feature):raise ValueError("INVALID_FINAL_FORENSIC_PROOF")
            feature=final_feature
        feature_cutoff=feature.get("decision_timestamp_ms")
        episode_cutoff=ep.get("decision_timestamp_ms") if final_feature else ep.get("canonical_decision_timestamp_ms",ep.get("base_decision_timestamp_ms",ep.get("decision_timestamp_ms")))
        if verify_feature_snapshot_v1793127(feature) and feature_cutoff!=episode_cutoff:
            raise ValueError("FORENSIC_SOURCE_TIMESTAMP_MISMATCH")
        if canonical_id in canonical_seen:raise ValueError("DUPLICATE_CANONICAL_FORENSIC_OPPORTUNITY")
        canonical_seen.add(canonical_id)
        primary,labels=classify_observed_loss_v1793127(ep,feature)
        output.append({"opportunity_id":oid,"observed_execution":dict(ep),
                       "canonical_opportunity_id":canonical_id,
                       "pre_entry_snapshot":feature.get("causal_snapshot"),
                       "primary_label":primary,"observation_tags":labels,
                       "inferred_hypothesis":"UNKNOWN","counterfactual_assessment_status":"NOT_ESTABLISHED",
                       "confidence":"UNKNOWN" if primary=="OTHER/UNKNOWN" else "OBSERVED_NOT_CAUSAL",
                       "source_proof_valid":verify_feature_snapshot_v1793127(feature)})
    return output
