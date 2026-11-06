"""Measure archived first rejections without replaying or altering V127."""

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def diagnose(run, dataset):
    manifest = json.loads((run / "artifact_sha256_manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((run / "00_run_manifest.json").read_text(encoding="utf-8"))
    with dataset.open("rb") as stream:
        dataset_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if dataset_digest != metadata["dataset_sha256"]:
        raise ValueError("Canonical dataset differs from archived run")
    inputs = {}

    def verified(name):
        path = run / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != manifest["files"][name]:
            raise ValueError(f"Archived artifact hash mismatch: {name}")
        inputs[name] = digest
        return path

    identities = {}
    selected = defaultdict(Counter)
    for row in rows(verified("10_canonical_decision_inputs.jsonl.gz")):
        oid = row["opportunity_id"]
        if oid in identities:
            raise ValueError(f"Duplicate archived opportunity: {oid}")
        group = f'{row["model_id"]}/{row["direction"]}'
        identities[oid] = group, row["setup_episode_id"], row["decision_timestamp_ms"]
        for field in ("selected_anchor_id", "selected_target_id", "selected_sweep_id", "selected_break_id", "selected_fvg_id"):
            selected[group][f"{field}:{'present' if row.get(field) else 'missing'}"] += 1

    policies = {}
    for path in sorted(run.glob("SI[1-9]_*_decisions.jsonl.gz")):
        counts = defaultdict(Counter)
        setups = defaultdict(set)
        seen = set()
        for row in rows(verified(path.name)):
            oid = row["opportunity_id"]
            group, setup, timestamp = identities[oid]
            if oid in seen or timestamp != row["decision_timestamp_ms"]:
                raise ValueError(f"Inconsistent archived policy decision: {oid}")
            seen.add(oid)
            reason = row["rejection_reason"] or row["status"]
            counts[group][reason] += 1
            setups[group, reason].add(setup)
        policies[path.name.removesuffix("_decisions.jsonl.gz")] = {
            group: {reason: {"decisions": count, "distinct_setup_episodes": len(setups[group, reason])}
                    for reason, count in reasons.items()}
            for group, reasons in counts.items()
        }
    if len(policies) != 9:
        raise ValueError("Expected nine archived research policy decision ledgers")
    return {"scope": "Archived first recorded decision rejection; not a new backtest or full stage funnel",
            "archived_executable_sha": metadata["tested_executable_sha"], "dataset_sha256": dataset_digest,
            "input_sha256": inputs, "canonical_decisions": len(identities),
            "selected_metadata_by_model_side": selected, "policy_first_rejection_by_model_side": policies}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = diagnose(args.run, args.dataset)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
