"""Post-run documentation derived only from frozen source and completed raw artifacts.

Does not change executable source, raw ledgers, or acceptance verdicts.
Run only after the main verifier finishes successfully.
"""
from __future__ import annotations
import ast
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Tuple

APP = Path(r"D:\build app-crypto\aurum-desk")
REPO = APP.parent
OLD = "3ed8f8af337a1a065da265861f95ddd2d7f625a4"
GIT = r"C:\Program Files\Git\mingw64\bin\git.exe"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def historical_cohort_audit(run):
    model_names = ("SMC_M1_REVERSAL", "SMC_M1_CONTINUATION", "SMC_M1_FVG_CONTINUATION", "SMC_M1_SWEEP_RECLAIM", "CG01_SWEEP_MSS_FVG")
    aliases = {name: name for name in model_names}
    aliases.update(ICT_2022_MODEL="SMC_M1_CONTINUATION", UNICORN_MODEL="SMC_M1_FVG_CONTINUATION", SILVER_BULLET="SMC_M1_SWEEP_RECLAIM")
    raw = list(rows(run / "13_baseline_raw_execution_episodes.jsonl.gz"))
    joined = {r["opportunity_id"]: r for r in rows(run / "13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz")}
    archive_path = APP / "docs/artifacts/v17_9_3_1_2_6/v17_9_3_1_2_6_20261103T021857Z_3a94182c9839/08_per_trade_loss_forensics_aggregate.json"
    archive = read(archive_path)
    digest_path = archive_path.with_name("09_per_trade_forensics_raw_index_and_digest.json")
    digest_claim = read(digest_path)
    historical_payload = {k: v for k, v in archive.items() if k != "tested_executable_sha"}
    historical_payload["schema_version"] = "17.9.3.1.2.3"
    historical_digest = hashlib.sha256(json.dumps(historical_payload, sort_keys=True).encode("utf-8")).hexdigest()
    if historical_digest != digest_claim["forensics_sha256_digest"]:
        raise RuntimeError("Archived full forensic ledger does not match its original pre-metadata SHA256 digest")
    archived_rows = [row for key in ("closed_trade_forensic_ledger", "open_at_horizon_forensic_ledger", "data_gap_forensic_ledger") for row in archive[key]]
    archived_index = {row["opportunity_id"]: row for row in archived_rows}
    raw_ids = {row["opportunity_id"] for row in raw}
    excluded = [row for row in raw if row["opportunity_id"] not in archived_index]
    if any(row.get("outcome_label") != "NO_FORWARD_BARS" or row.get("filled") is not False
           or row.get("realized_net_pnl_usd") != 0 for row in excluded):
        raise RuntimeError("Unexpected baseline episode outside the archived forensic cohort")
    cohort = [row for row in raw if row["opportunity_id"] in archived_index]
    if (len(archived_index) != len(archived_rows) or len(raw_ids) != len(raw)
            or len(joined) != len(raw) or set(joined) != raw_ids or len(cohort) != len(archived_rows)):
        raise RuntimeError("Archived/new baseline forensic identities are missing, duplicated or changed")
    for ep in cohort:
        previous = archived_index[ep["opportunity_id"]]
        expected_identity = (previous["decision_timestamp_ms"], previous["direction"], previous["outcome_label"], previous["legacy_model_id"], previous["realized_net_pnl_usd"])
        actual_identity = (ep["decision_timestamp_ms"], ep["direction"], ep["outcome_label"], ep.get("source_model_id", ep["model_id"]), round(float(ep.get("realized_net_pnl_usd") or 0), 4))
        if expected_identity != actual_identity:
            raise RuntimeError("Archived baseline accounting/decision identity changed: " + ep["opportunity_id"])
    counts = Counter()
    by_model = defaultdict(Counter)
    migration = defaultdict(Counter)
    ledger = []
    groups = {key: defaultdict(Counter) for key in ("model", "direction", "session", "regime", "exit_reason")}
    for ep in cohort:
        if not ep.get("filled") or ep.get("outcome_label") != "LOSS":
            continue
        oid = ep["opportunity_id"]
        historical = archived_index[oid]
        previous = historical["primary_root_cause"]
        labels = historical["root_cause_labels"]
        now = joined[oid]
        canonical_model = aliases.get(ep.get("model_id"), ep.get("model_id", "UNKNOWN"))
        counts[previous] += 1
        by_model[canonical_model][previous] += 1
        migration[previous][now["primary_label"]] += 1
        values = dict(model=canonical_model, direction=ep.get("direction", "UNKNOWN"),
                      session=ep.get("session", ep.get("session_label", "UNKNOWN")),
                      regime=ep.get("regime", ep.get("regime_label", "UNKNOWN")),
                      exit_reason=ep.get("exit_reason", "UNKNOWN"))
        for key, value in values.items():
            groups[key][str(value)][now["primary_label"]] += 1
        ledger.append(dict(opportunity_id=oid, canonical_opportunity_id=now["canonical_opportunity_id"],
                           previous_label_archived=previous, current_observation_label=now["primary_label"],
                           inferred_hypothesis=now["inferred_hypothesis"], source_proof_valid=now["source_proof_valid"],
                           previous_classifier_labels=labels, confidence=now["confidence"], groups=values,
                           unknown_evidence_reasons=[dict(feature=ref["evidence_type"],reason=ref["reason_code"])
                               for ref in (now.get("pre_entry_snapshot") or {}).get("features",[]) if ref["proof_state"]=="UNKNOWN"]))
    expected = {m: data["primary_loss_causes"] for m, data in archive["by_model_breakdown"].items() if data["losses_count"]}
    actual = {key: dict(value) for key, value in by_model.items()}
    exact = actual == expected and dict(counts) == {"OTHER/UNKNOWN": 109, "TARGET_NOT_REAL_OR_ALREADY_SWEPT": 48}
    joined_exact = len(joined) == len(raw) and all(ep["opportunity_id"] in joined for ep in raw)
    if not exact or not joined_exact:
        raise RuntimeError("Archived 109 UNKNOWN / 48 TARGET cohorts are not conserved exactly")
    for name in model_names:
        groups["model"].setdefault(name, Counter())
    loss_labels = Counter(row["current_observation_label"] for row in ledger)
    output = dict(status="VERIFIED_ARCHIVED_COHORT_MIGRATION",
                  historical_raw_per_trade_artifact_available=True,
                  method="Use the 241 actual archived forensic rows; validate their original complete ledger digest and match every baseline ID, decision timestamp, direction, model, outcome and net accounting before migrating labels.",
                  archived_tested_executable_sha=archive["tested_executable_sha"],
                  archived_full_forensic_digest_recomputed=historical_digest,
                  archived_original_digest_claim=digest_claim["forensics_sha256_digest"],
                  archived_digest_file_sha256=sha(digest_path),
                  archived_aggregate_sha256=sha(archive_path),
                  raw_episode_count=len(raw),
                  archived_cohort_episode_count=len(cohort),
                  excluded_unfilled_no_forward_bars_ids=[row["opportunity_id"] for row in excluded],
                  raw_outcome_counts=dict(Counter(ep.get("outcome_label", "UNKNOWN") for ep in raw)),
                  canonical_join_complete=joined_exact, archived_per_model_counts_equal=exact,
                  legacy_loss_labels=dict(counts), migration={k: dict(v) for k, v in migration.items()},
                  current_loss_observation_labels=dict(loss_labels), current_loss_unknown_fraction=loss_labels["OTHER/UNKNOWN"]/len(ledger),
                  groups={k: {name: dict(labels) for name, labels in group.items()} for k, group in groups.items()},
                  grouped_missing_value_policy="UNKNOWN; no session/regime/exit cause inferred from trade outcome.",
                  per_trade_verified_migration=ledger,
                  baseline_raw_sha256=sha(run / "13_baseline_raw_execution_episodes.jsonl.gz"),
                  source_join_sha256=sha(run / "13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz"))
    write(run / "14_legacy_109_unknown_48_target_verified_migration.json", output)


def supplement(run):
    run_manifest = read(run / "00_run_manifest.json")
    if run_manifest.get("prepublication_engineering") != "PASS" or run_manifest.get("failure"):
        raise RuntimeError("Main frozen verification has not passed; supplements cannot relabel it.")
    head = subprocess.check_output([GIT, "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    branch = subprocess.check_output([GIT, "branch", "--show-current"], cwd=REPO, text=True).strip()
    if head != run_manifest["tested_executable_sha"] or branch != "feature/v17-9-2-structural-sl-tp-from-qa":
        raise RuntimeError("Supplement must read the exact tested source on the authorized branch before publication commits")
    historical_cohort_audit(run)
    write(run / "00_publication_supplement_index.json", dict(
        branch=branch, environment_manifest="02_environment_package_manifest.json",
        function_impact_map="02_source_to_consumer_function_impact_map.json"))
    inventory = read(run / "02_source_function_and_consumer_inventory.json")
    extra_files = (
        "aurum-desk/backend/tests/conftest.py",
        "aurum-desk/backend/services/research_resource_guard.py",
        "aurum-desk/backend/domain_calculator.py",
        "aurum-desk/backend/services/stop_placement_service.py",
        "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py",
        "aurum-desk/backend/services/liquidity_scenario_service.py",
        "aurum-desk/backend/services/htf_structure_service.py",
        "aurum-desk/backend/services/unicorn_evidence_service.py",
        "aurum-desk/tools/run_v17_9_3_1_2_2_entry_signal_backtest.py",
        "aurum-desk/tools/run_v17_9_3_1_2_2_verification.py",
        "aurum-desk/tools/fresh_junit_io.py",
    )
    for rel in extra_files:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        inventory.setdefault(rel, [dict(function=node.name, line=node.lineno,
                                        contract=ast.get_docstring(node) or "",
                                        symbol_kind="class" if isinstance(node, ast.ClassDef) else "function")
                                   for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))])
    critical = ("derive_pre_entry_features", "build_verified_baseline", "required_proof_rejection", "rebuild_materialized_intent", "build_forensic_causal", "classify_observed_loss", "classify_target_failure", "compute_matched_policy_ab", "compute_policy_decision_diff", "verify_matched_ab", "verify_fresh_full_junit", "verify_raw_accounting", "verify_git_remote", "evaluate_signal_intelligence_gate", "replay_single_si_policy", "run_v17_9_3_1_2_2_entry_signal_backtest_pipeline", "discover_research_candidates", "_eval_checkpoints_chunk", "execute_independent_worker",
                "_sha256_file_bounded", "guard_runtime_database", "_bounded_worker_candidate_index", "_CompressedCheckpointDecisions", "_MergedCheckpointDecisions", "collect_causal_stop_anchor", "rank_structure_valid_anchors", "derive_structure_valid_stop", "resolve_causal_target_candidates", "select_viable_structural_target", "M1ScalpRiskCalculator", "calculate", "build_market_decision_context", "evaluate_model_direction", "build_signal_quality_evidence", "score_candidate_record_pre_entry", "arbitrate_competing_model_intents", "advance_pending_confirmation_state", "compute_strategy_statistical_verdict", "run_full_backend_pytest", "run_16_executable", "compute_stop_atr", "identify_confirmed_swings", "evaluate_htf_structure", "detect_unicorn_evidence", "raise_if_research_aborted", "_guarded_research_call", "run_independent_source_reconstruction")
    entries = []
    for rel, functions in inventory.items():
        source_path = REPO / rel
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for record in functions:
            if not any(record["function"].startswith(prefix) for prefix in critical):
                continue
            name = record["function"]
            consumers = []
            for candidate_rel in inventory:
                candidate_tree = tree if candidate_rel == rel else ast.parse((REPO / candidate_rel).read_text(encoding="utf-8"))
                for node in ast.walk(candidate_tree):
                    if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == name) or (isinstance(node.func, ast.Attribute) and node.func.attr == name)):
                        consumers.append(dict(file=candidate_rel, line=node.lineno, callee_expression=ast.unparse(node.func), match_kind="STATIC_NAME_MATCH_NOT_TYPE_RESOLVED"))
            entries.append(dict(file=rel, **record, callsites_in_audited_source_inventory=consumers))
    write(run / "02_source_to_consumer_function_impact_map.json", dict(tested_executable_sha=run_manifest["tested_executable_sha"], scope="AST symbol-name callsite matches in explicitly listed frozen files. Attribute receiver types and indirect pytest fixture calls are not resolved; this is not an assertion of complete dynamic reachability.", functions=entries))
    junit = read(run / "18_independent_junit_verification.json")
    controls = [name for name in junit["passed_node_ids"] if "v17_9_3_1_2_7" in name]
    new_mutants = read(run / "19_new_executable_red_green_mutation_evidence.json")
    write(run / "04_baseline_feature_fabrication_before_after_and_mutation_repro.json", dict(before_source_sha=OLD, after_source_sha=run_manifest["tested_executable_sha"], before="M1 body/model-name and optimistic defaults were substituted for structural proof.", after="Raw closed-OHLC-derived tri-state evidence, source hashes and final intent geometry binding; missing proof rejects the research intent.", executable_mutation_evidence="19_new_executable_red_green_mutation_evidence.json", mutation_status=new_mutants["status"], executed_control_nodes=controls))
    write(run / "08_missing_evidence_fail_closed_counterexamples.json", dict(source="18_independent_junit_verification.json", passed_node_ids=[n for n in controls if any(word in n.lower() for word in ("unknown", "missing", "future", "partial", "tamper", "duplicate"))], note="These are actual executed test identifiers; input details and assertions are in the frozen test source, not fabricated examples."))
    ab = []
    manifest = read(run / "artifact_sha256_manifest.json")
    for spec in manifest["ab_specs"]:
        ab.append(spec)
    write(run / "11_matched_ab_comparison_file_index.json", dict(comparisons=ab, independent_disk_verification="24_disk_integrity_check.json", pair_ledgers="One compressed JSONL pair file per comparison and fill mode, as referenced by the manifest ab_specs; no duplicate merged corpus."))
    required_mapping = {
        "06_verified_causal_evidence_contract_and_source_refs.json": ["06_causal_evidence_contract_and_proof_state_counts.json", "07_verified_causal_features.jsonl.gz"],
        "07_baseline_and_discovery_feature_proof_digest_summary.json": ["07_independent_source_reconstruction.json"],
        "09_si0_si7_si8_si9_policy_decision_diff_summary.json": ["11_true_matched_ab_summary_and_independent_verification.json"],
        "10_raw_decision_intents_and_proof_hash_index.json": ["10_canonical_decision_inputs.jsonl.gz", "artifact_sha256_manifest.json"],
        "12_matched_ab_pair_ledger.jsonl.gz": [str(p.name) for p in sorted(run.glob("*_pairs.jsonl.gz"))],
        "19_old_and_new_executable_red_green_mutation_evidence.json": ["19_old_executable_red_green_mutation_evidence.json", "19_new_executable_red_green_mutation_evidence.json"],
        "20_gate_negative_controls_and_independent_reverification.json": ["20_executed_negative_control_node_index.json", "18_independent_junit_verification.json", "24_disk_integrity_check.json"],
        "25_git_phase_a_b_c_remote_push_receipt.json": ["Separate publication receipt committed after the Phase B push; final Phase C SHA observed externally to avoid a self-referential digest."],
    }
    write(run / "00_required_artifact_name_map.json", required_mapping)
    guide = """# Hàm nào làm gì trong V17.9.3.1.2.7

Đường dẫn và số dòng chính xác nằm trong `02_source_to_consumer_function_impact_map.json`.
Số dòng áp dụng cho commit executable được ghi trong `00_run_manifest.json`.

| File / hàm | Trách nhiệm và dữ liệu đi qua |
|---|---|
| `shared_strategy_evaluator.py`: `build_market_decision_context`, `SharedStrategyEvaluator.evaluate_model_direction` | Chuẩn bị OHLC đóng theo thời điểm quyết định, dữ liệu đa khung và nguồn quote; đánh giá mô hình/hướng. Fingerprint chứa nội dung chuỗi dữ liệu và provenance quote để tránh dùng lại cache cho đầu vào khác. |
| `domain_calculator.py`: `M1ScalpRiskCalculator`; `v17_9_2_structural_stop_intelligence.py`: `collect_causal_stop_anchor_candidates`, `rank_structure_valid_anchors`, `derive_structure_valid_stop`, `resolve_causal_target_candidates`, `select_viable_structural_target` | Tái sử dụng bộ tính risk/cost/size và chọn anchor/target cấu trúc hiện có. Bằng chứng causal mới kiểm tra intent được chọn; không thay điều kiện rủi ro bằng các giá trị mặc định thuận lợi. |
| `v17_9_3_1_2_7_causal_signal_truth.py`: `derive_pre_entry_features_from_causal_context_v1793127` | Từ OHLC thực, dựng bằng chứng swing/HTF, sweep-reclaim, break-displacement, FVG-touch và Unicorn; gắn nguồn, thời điểm, hash và geometry. Không có bằng chứng thì UNKNOWN. |
| Cùng file: `build_verified_baseline_pre_entry_feature_ledger_v1793127` | Dựng feature cho từng cơ hội baseline bằng dữ liệu đã biết ở thời điểm đó. Không dùng tên model, body ratio hoặc giá trị mặc định True để thay bằng chứng cấu trúc. |
| Cùng file: `required_proof_rejection_reason_v1793127` | Kiểm tra hợp đồng bằng chứng và các điều kiện bắt buộc theo mô hình. Thiếu/UNKNOWN/sai hash/sai thời điểm/sai geometry dẫn đến từ chối intent nghiên cứu. Tra tên chính xác trong inventory nếu signature thay đổi. |
| `v17_9_3_1_2_2_signal_intelligence.py`: `evaluate_signal_intelligence_gate_v1793122` | Áp dụng kiểm tra vào intent đề xuất khi bật chế độ nghiên cứu yêu cầu causal truth. OBSERVE_ONLY giữ hành động baseline; đường thực thi experimental không được tự kích hoạt. |
| `v17_9_3_1_2_3_core_trading_logic_repair.py`: `_eval_checkpoints_chunk_v1793125`, `discover_research_candidates_v1793123` | Quét checkpoint M1 đóng cho 5 model × 2 hướng; chạy OFF/SI7/SI8/OBSERVE và ghi fingerprint hành động thực. Lưu checkpoint độc lập theo batch. |
| `run_v17_9_3_1_2_2_entry_signal_backtest.py`: `replay_single_si_policy_v1793122` | Chặn bằng chứng chưa được chứng minh trước arbitration. Sau khi materialize entry/SL/TP/quantity, dựng và kiểm tra lại bằng chứng của intent cuối; lưu quyết định được nhận và bị loại. |
| `v17_9_3_1_2_7_causal_signal_truth.py`: `rebuild_materialized_intent_proof_v1793127` | Kiểm tra lại tại thời điểm và geometry thực của intent mới; không mang nguyên proof cũ sang entry hoặc anchor/target đã đổi. |
| `v17_9_3_1_2_7_matched_ab.py`: `compute_policy_decision_diff_v1793127`, `compute_matched_policy_ab_v1793127` | Ghép cơ hội bằng ID + thời điểm + hash nguồn, từ chối trùng/khác nguồn. Tách paired closed-R delta, tổng net USD realized, unmatched và censored. Hai policy có thể thật sự giống nhau; không bắt buộc phải tạo khác biệt. |
| Cùng file: `verify_matched_ab_against_raw_ledgers_v1793127` | Đọc lại các ledger riêng trên đĩa, tính lại pairing/accounting, đối chiếu báo cáo và kiểm tra đường dẫn/file bị thiếu hoặc sửa. |
| `v17_9_3_1_2_7_causal_signal_truth.py`: `build_forensic_causal_evidence_join_v1793127`, `classify_observed_loss_v1793127`, `classify_target_failure_modes_v1793127` | Ghép ID legacy với cơ hội canonical đúng nguồn/thời điểm. Tách quan sát từ dữ liệu và giả thuyết nguyên nhân; RR cao không chứng minh target đã quét. Không đủ proof thì giữ UNKNOWN. |
| `v17_9_3_1_2_7_proof_checks.py`: `verify_fresh_full_junit` | Đối chiếu chính xác multiset node được collect với node thực chạy, exit code, hash và độ mới của JUnit. Skip được báo riêng. |
| Cùng file: `reconstruct_feature_sources_from_disk`, `verify_raw_accounting_and_worker_proofs` | Tiến trình độc lập dựng lại feature từ quyết định và OHLC, rồi tính lại fees/R/PnL, thống kê, OOS, forensic và binding hash worker vào raw ledger. |
| `v17_9_3_1_2_7_source_mutations.py`: `run_new_source_mutations` | Tạo 12 lỗi source thật trong package tạm; yêu cầu test fail RED rồi pass GREEN sau khôi phục, cùng tập test thực chạy. |
| `backend/tests/conftest.py`: `_sha256_file_bounded`, `guard_runtime_database` | Hash toàn bộ runtime DB theo khối 1 MiB; giữ nguyên kiểm tra inode/size/hash trước-sau. Tránh cấp phát nguyên file 3,31 GiB vào RAM trong mỗi shard. |
| `research_resource_guard.py`: `raise_if_research_aborted`; `run_v17_9_3_1_2_2_entry_signal_backtest.py`: `_guarded_research_call` | Đọc abort marker tại checkpoint và trước/sau queued task; truyền lỗi tài nguyên về luồng cha để không tiếp tục các giai đoạn sau khi guard đã dừng. |
| `fresh_junit_io.py`: `read_closed_junit_artifact` | Trên Windows, mở handle đọc không cho chia sẻ ghi/xóa; đợi writer đóng trong thời hạn, lấy bytes và metadata từ cùng handle. Producer và auditor cùng dùng primitive này; vẫn từ chối thay đổi timestamp hoặc nội dung sau attestation. |
| `run_v17_9_3_1_2_7_verification.py`: `run_independent_source_reconstruction` | Ghi raw output trước D, giải phóng corpus phía cha, ghi lỗi child rõ ràng; G05 chỉ được PASS sau khi tái dựng độc lập thành công. Auditor vẫn dựng snapshot đầy đủ nhưng chỉ giữ hash để đối chiếu. |
| `run_v17_9_3_1_2_2_entry_signal_backtest.py`: `_bounded_worker_candidate_index_v1793127`, `_CompressedCheckpointDecisionsV1793127`, `_MergedCheckpointDecisionsV1793127` | Nén input worker không mất dữ liệu, đọc và ghép theo checkpoint, không giữ thêm các view plain không dùng. Ledger đầu vào có thể dùng chung; quyết định, fill, A/B và forensic được mỗi lượt worker tự tính lại. |
| `run_v17_9_3_1_2_7_verification.py`: `main`, `verify_v1793127_artifact_truth` | Đóng băng SHA, chạy full regression → mutation → discovery/replay → tái dựng độc lập; giám sát tài nguyên, lập manifest và gate. Thiếu proof không được tự PASS. |
| `v17_9_3_1_2_7_proof_checks.py`: `verify_git_remote_phase_receipt_v1793127` | Sau publication, đọc SHA local/remote thật, kiểm tra ancestry và source không đổi từ commit đã test. SHA Phase C cuối được quan sát bên ngoài để tránh tự tham chiếu. |

Luồng đọc: context OHLC → proof causal → gate nghiên cứu → arbitration → intent cuối → proof cuối → replay ba fill mode → raw ledgers → A/B/forensic → verifier độc lập → commit/push receipt.

Giới hạn: quote dùng trong nghiên cứu là synthetic close ± 0,10 USD, không phải bid/ask provider thực. Lịch sử đã được xem ở phiên bản trước không trở thành fresh OOS chỉ vì chạy lại. PASS kỹ thuật không chứng minh có lợi thế kinh tế. Đọc verdict thống kê và sample size riêng; activation vẫn NO.
"""
    # Validate every claimed function spelling against the complete frozen inventory.
    actual_names = {item["function"] for items in inventory.values() for item in items}
    optional_alias = "required_proof_rejection_reason_v1793127"
    if optional_alias not in actual_names:
        replacement = next((name for name in actual_names if name.startswith("required_proof_rejection")), None)
        if replacement is None:
            raise RuntimeError("Required-proof gate function missing from frozen inventory")
        guide = guide.replace(optional_alias, replacement)
    (run / "HUONG_DAN_HAM_VA_LUONG_KIEM_CHUNG.md").write_text(guide, encoding="utf-8", newline="\n")
    # The supplement script itself is part of the provenance, with no dependency on a mutable workspace path.
    (run / "supplement_v127_evidence.py").write_bytes(Path(__file__).read_bytes())
    manifest["files"] = {str(p.relative_to(run)).replace("\\", "/"): sha(p) for p in run.rglob("*") if p.is_file() and p.name != "artifact_sha256_manifest.json" and not p.name.endswith(".partial")}
    write(run / "artifact_sha256_manifest.json", manifest)
    print(json.dumps(dict(supplement_files=len(manifest["files"]), cohort=read(run / "14_legacy_109_unknown_48_target_verified_migration.json")["status"])))


if __name__ == "__main__":
    supplement(Path(sys.argv[1]).resolve())
