"""
V12_2 Acceptance Manifest: 74 Requirements Definition and Status Tracker.
Strictly decoupled from pre-assigned statuses: actual status is determined by real test execution evidence.
"""
from typing import Dict, Any, List, Optional
import os
import json

ALL_74_REQUIREMENTS: List[Dict[str, Any]] = [
    # Accounting (16)
    {"id": "A01", "group": "ACCOUNTING", "objective": "Oracle SHORT full precision, fixed quantity và effective fees đúng", "mapped_test": "test_v12_2_acceptance.py::test_a01_oracle_short_accounting"},
    {"id": "A02", "group": "ACCOUNTING", "objective": "Oracle LONG mirror, gross/net/cash", "mapped_test": "test_v12_2_acceptance.py::test_a02_oracle_long_mirror"},
    {"id": "A03", "group": "ACCOUNTING", "objective": "Fee keyword sai bị reject hoặc explicit alias; không ignored extras", "mapped_test": "test_v12_2_acceptance.py::test_a03_fee_keyword_validation"},
    {"id": "A04", "group": "ACCOUNTING", "objective": "Net RR 1.99996 fail ngưỡng 2; raw calculation/export thống nhất", "mapped_test": "test_v12_2_acceptance.py::test_a04_net_rr_threshold_unrounded"},
    {"id": "A05", "group": "ACCOUNTING", "objective": "Invalid LONG/SHORT geometry bị chặn ở mọi entry path", "mapped_test": "test_v12_2_acceptance.py::test_a05_invalid_geometry_blocked"},
    {"id": "A06", "group": "ACCOUNTING", "objective": "Planned vs actual RR khác khi fill price thay đổi", "mapped_test": "test_v12_2_acceptance.py::test_a06_planned_vs_actual_rr_difference"},
    {"id": "A07", "group": "ACCOUNTING", "objective": "Taker TP, resting maker TP, marketable limit role", "mapped_test": "test_v12_2_acceptance.py::test_a07_tp_maker_taker_cost_model"},
    {"id": "A08", "group": "ACCOUNTING", "objective": "Slippage trong fill không trừ lần hai tại entry/exit/stress", "mapped_test": "test_v12_2_acceptance.py::test_a08_slippage_no_double_deduction"},
    {"id": "A09", "group": "ACCOUNTING", "objective": "Entry fee, exit fee, funding ledger exactly once", "mapped_test": "test_v12_2_acceptance.py::test_a09_fee_legs_posting_exactly_once"},
    {"id": "A10", "group": "ACCOUNTING", "objective": "Fixed quantity đổi leverage chỉ đổi margin, không PnL/risk", "mapped_test": "test_v12_2_acceptance.py::test_a10_fixed_quantity_leverage_invariance"},
    {"id": "A11", "group": "ACCOUNTING", "objective": "Quantity floor/min quantity/min notional/multiplier/tick precision", "mapped_test": "test_v12_2_acceptance.py::test_a11_quantity_precision_and_limits"},
    {"id": "A12", "group": "ACCOUNTING", "objective": "Vị thế mở qua ngày VN/tháng: cash + MTM carry đúng", "mapped_test": "test_v12_2_acceptance.py::test_a12_cross_day_cash_mtm_carry"},
    {"id": "A13", "group": "ACCOUNTING", "objective": "Daily-peak drawdown khác cumulative-peak drawdown trong fixture", "mapped_test": "test_v12_2_acceptance.py::test_a13_daily_vs_cumulative_drawdown"},
    {"id": "A14", "group": "ACCOUNTING", "objective": "Gap loss có thể lớn hơn planned risk và được ghi đúng", "mapped_test": "test_v12_2_acceptance.py::test_a14_gap_loss_exceeding_planned_risk"},
    {"id": "A15", "group": "ACCOUNTING", "objective": "Legacy position snapshots không bị economic rewrite sau upgrade", "mapped_test": "test_v12_2_acceptance.py::test_a15_legacy_snapshot_economic_integrity"},
    {"id": "A16", "group": "ACCOUNTING", "objective": "Nhiều posting nhỏ vẫn reconcile full precision", "mapped_test": "test_v12_2_acceptance.py::test_a16_multi_posting_ledger_reconciliation"},

    # Data and Causality (14)
    {"id": "B01", "group": "DATA_CAUSALITY", "objective": "Provider -> replay -> Excel metadata end-to-end, bắt D01", "mapped_test": "test_v12_2_acceptance.py::test_b01_metadata_end_to_end_no_double_nesting"},
    {"id": "B02", "group": "DATA_CAUSALITY", "objective": "Required frame thiếu ghi MISSING, không NOT_USED", "mapped_test": "test_v12_2_acceptance.py::test_b02_required_frame_missing_label"},
    {"id": "B03", "group": "DATA_CAUSALITY", "objective": "Actual count/hash/gap/quarantine từ fixture", "mapped_test": "test_v12_2_acceptance.py::test_b03_dynamic_quality_metrics"},
    {"id": "B04", "group": "DATA_CAUSALITY", "objective": "Cache corrupt/finite/geometry/dedupe/coverage được validate", "mapped_test": "test_v12_2_acceptance.py::test_b04_cache_integrity_validation"},
    {"id": "B05", "group": "DATA_CAUSALITY", "objective": "Pagination ordered/reversed/stalled/retry fail/empty early", "mapped_test": "test_v12_2_acceptance.py::test_b05_pagination_robustness"},
    {"id": "B06", "group": "DATA_CAUSALITY", "objective": "First/end/internal coverage và market closure classification", "mapped_test": "test_v12_2_acceptance.py::test_b06_coverage_and_market_closures"},
    {"id": "B07", "group": "DATA_CAUSALITY", "objective": "Forming candle bị loại tại cutoff, eval counts dùng close_at", "mapped_test": "test_v12_2_acceptance.py::test_b07_forming_candle_cutoff_exclusion"},
    {"id": "B08", "group": "DATA_CAUSALITY", "objective": "Prefix invariance có pending/closed/open position và suffix mutation", "mapped_test": "test_v12_2_acceptance.py::test_b08_prefix_invariance"},
    {"id": "B09", "group": "DATA_CAUSALITY", "objective": "Final open MTM dùng last processed, không dataset last", "mapped_test": "test_v12_2_acceptance.py::test_b09_final_open_mtm_no_future_last_bar"},
    {"id": "B10", "group": "DATA_CAUSALITY", "objective": "Pivot/FVG/news/rules confirmed/known_at <= clock", "mapped_test": "test_v12_2_acceptance.py::test_b10_pivots_known_at_causality"},
    {"id": "B11", "group": "DATA_CAUSALITY", "objective": "Frozen instrument metadata không thay global runtime cache", "mapped_test": "test_v12_2_acceptance.py::test_b11_instrument_freeze_isolation"},
    {"id": "B12", "group": "DATA_CAUSALITY", "objective": "Dataset/config/cost hash deterministic, 5M input lineage đúng", "mapped_test": "test_v12_2_acceptance.py::test_b12_dataset_config_hash_lineage"},
    {"id": "B13", "group": "DATA_CAUSALITY", "objective": "Custom data thiếu HTF/5M reject hoặc PARITY_PARTIAL rõ", "mapped_test": "test_v12_2_acceptance.py::test_b13_custom_data_missing_frames_handling"},
    {"id": "B14", "group": "DATA_CAUSALITY", "objective": "Không nội suy 15M->5M; aggregation có coverage thật", "mapped_test": "test_v12_2_acceptance.py::test_b14_no_interpolation_15m_to_5m"},

    # NY Strategy (22)
    {"id": "C01", "group": "NY_STRATEGY", "objective": "Pre-NY end 08:25 loại nến đóng 08:30", "mapped_test": "test_v12_2_acceptance.py::test_c01_pre_ny_range_excludes_0830_close"},
    {"id": "C02", "group": "NY_STRATEGY", "objective": "Frozen range không đổi tới 15:30; last60 không làm trôi range", "mapped_test": "test_v12_2_acceptance.py::test_c02_pre_ny_range_freeze_no_drift"},
    {"id": "C03", "group": "NY_STRATEGY", "objective": "B1 POI confirmation -> pullback -> 5M trigger đúng thứ tự", "mapped_test": "test_v12_2_acceptance.py::test_c03_setup_b1_chronology"},
    {"id": "C04", "group": "NY_STRATEGY", "objective": "B1 thiếu POI/BOS/alignment/target trả đúng blocker", "mapped_test": "test_v12_2_acceptance.py::test_c04_setup_b1_blockers"},
    {"id": "C05", "group": "NY_STRATEGY", "objective": "B2 breakout -> retest -> confirm đúng chronology", "mapped_test": "test_v12_2_acceptance.py::test_c05_setup_b2_break_retest_chronology"},
    {"id": "C06", "group": "NY_STRATEGY", "objective": "Retest trước breakout hoặc cùng OHLC bar không pass", "mapped_test": "test_v12_2_acceptance.py::test_c06_setup_b2_forbid_same_bar_retest"},
    {"id": "C07", "group": "NY_STRATEGY", "objective": "Range invalidation, expiry và setup identity dedup", "mapped_test": "test_v12_2_acceptance.py::test_c07_setup_b2_invalidation_and_dedup"},
    {"id": "C08", "group": "NY_STRATEGY", "objective": "Target model explicit; range extension riêng liquidity target", "mapped_test": "test_v12_2_acceptance.py::test_c08_target_model_explicit_tagging"},
    {"id": "C09", "group": "NY_STRATEGY", "objective": "Trigger 08:35/08:40 được xử lý theo 5M", "mapped_test": "test_v12_2_acceptance.py::test_c09_5m_cadence_trigger_detection"},
    {"id": "C10", "group": "NY_STRATEGY", "objective": "Baseline signal không bị nhân ba bởi scheduler 5M", "mapped_test": "test_v12_2_acceptance.py::test_c10_baseline_no_triple_counting_on_5m"},
    {"id": "C11", "group": "NY_STRATEGY", "objective": "Quota thiếu target bị chặn, không tạo TP bằng 2.5SL", "mapped_test": "test_v12_2_acceptance.py::test_c11_quota_no_synthetic_tp_stretching"},
    {"id": "C12", "group": "NY_STRATEGY", "objective": "H1/H4 conflict không fallback thành aligned", "mapped_test": "test_v12_2_acceptance.py::test_c12_h1_h4_trend_conflict_blocked"},
    {"id": "C13", "group": "NY_STRATEGY", "objective": "Candidate pool/ranking/tie-break/expiration thực", "mapped_test": "test_v12_2_acceptance.py::test_c13_quota_candidate_pool_ranking"},
    {"id": "C14", "group": "NY_STRATEGY", "objective": "Custom deadline 14:00 hoạt động cả inner/outer; trước deadline bị chặn", "mapped_test": "test_v12_2_acceptance.py::test_c14_custom_deadline_evaluation"},
    {"id": "C15", "group": "NY_STRATEGY", "objective": "UI risk 0.10 -> API -> calculator -> Excel thống nhất", "mapped_test": "test_v12_2_acceptance.py::test_c15_ui_risk_propagation"},
    {"id": "C16", "group": "NY_STRATEGY", "objective": "Absent quality risk derive legacy risk; quota risk riêng", "mapped_test": "test_v12_2_acceptance.py::test_c16_absent_quality_risk_derivation"},
    {"id": "C17", "group": "NY_STRATEGY", "objective": "Minimum NY goal 1 khác maximum fills; daily cap 3 vẫn giữ", "mapped_test": "test_v12_2_acceptance.py::test_c17_goal_vs_cap_distinction"},
    {"id": "C18", "group": "NY_STRATEGY", "objective": "NY session qua VN midnight không cấp quota lần hai", "mapped_test": "test_v12_2_acceptance.py::test_c18_ny_session_cross_midnight_single_quota"},
    {"id": "C19", "group": "NY_STRATEGY", "objective": "Partial/missing/closed/open exposure eligibility và denominator có provenance", "mapped_test": "test_v12_2_acceptance.py::test_c19_eligibility_provenance_and_denominators"},
    {"id": "C20", "group": "NY_STRATEGY", "objective": "NY DST summer/winter, boundary và session attribution", "mapped_test": "test_v12_2_acceptance.py::test_c20_dst_transitions_handling"},
    {"id": "C21", "group": "NY_STRATEGY", "objective": "Attempt không phải fill; coverage 90% không ghi đạt mỗi ngày", "mapped_test": "test_v12_2_acceptance.py::test_c21_attempt_vs_fill_and_honest_coverage"},
    {"id": "C22", "group": "NY_STRATEGY", "objective": "Research variants không bật flags/settings production", "mapped_test": "test_v12_2_acceptance.py::test_c22_research_variants_disabled_in_prod"},

    # Production Parity and Guards (10)
    {"id": "D01", "group": "PARITY_GUARDS", "objective": "Production/replay cùng fixture và observations cho cùng states/decisions/risk/exit", "mapped_test": "test_v12_2_acceptance.py::test_d01_production_replay_parity"},
    {"id": "D02", "group": "PARITY_GUARDS", "objective": "Approved red lesson block; future/disabled rule không block sai", "mapped_test": "test_v12_2_acceptance.py::test_d02_lesson_rules_temporal_activation"},
    {"id": "D03", "group": "PARITY_GUARDS", "objective": "Yellow warnings/green evidence không override hard guards", "mapped_test": "test_v12_2_acceptance.py::test_d03_warnings_cannot_override_guards"},
    {"id": "D04", "group": "PARITY_GUARDS", "objective": "Historical news known_at/blackout boundary và missing news status", "mapped_test": "test_v12_2_acceptance.py::test_d04_historical_news_blackout_boundary"},
    {"id": "D05", "group": "PARITY_GUARDS", "objective": "Engine thực áp dụng cap3/consecutive2/loss budget/cooldown", "mapped_test": "test_v12_2_acceptance.py::test_d05_execution_coordinator_hard_guards"},
    {"id": "D06", "group": "PARITY_GUARDS", "objective": "READY khác FILLED; arm/expiry/cancel/trigger identity đúng", "mapped_test": "test_v12_2_acceptance.py::test_d06_ready_vs_filled_lifecycle"},
    {"id": "D07", "group": "PARITY_GUARDS", "objective": "Fill dùng observation sau quyết định, không range bar cũ", "mapped_test": "test_v12_2_acceptance.py::test_d07_fill_uses_subsequent_observation"},
    {"id": "D08", "group": "PARITY_GUARDS", "objective": "Fill-bar ambiguity và SL gap conservative, causal", "mapped_test": "test_v12_2_acceptance.py::test_d08_ambiguity_and_sl_gap_conservative"},
    {"id": "D09", "group": "PARITY_GUARDS", "objective": "Mock noti near-entry/READY/ARMED/FILLED/TP/SL/cancel, direction và outbox dedup", "mapped_test": "test_v12_2_acceptance.py::test_d09_notification_research_sink"},
    {"id": "D10", "group": "PARITY_GUARDS", "objective": "Sentinel chạy replay giữa observations; kiểm tra DB/WAL phù hợp, intercept runtime deps", "mapped_test": "test_v12_2_acceptance.py::test_d10_db_isolation_sentinel"},

    # Reporting, API and Jobs (12)
    {"id": "E01", "group": "REPORTING_JOBS", "objective": "Workbook 12 sheets có actual RR/fee legs/session/metadata", "mapped_test": "test_v12_2_acceptance.py::test_e01_workbook_12_sheets_content"},
    {"id": "E02", "group": "REPORTING_JOBS", "objective": "Daily 93 rows/partial/missing/month buckets và ledger reconciliation", "mapped_test": "test_v12_2_acceptance.py::test_e02_daily_rows_and_ledger_reconciliation"},
    {"id": "E03", "group": "REPORTING_JOBS", "objective": "Funnel unique vs occurrences thực; blockers không hardcoded", "mapped_test": "test_v12_2_acceptance.py::test_e03_funnel_and_blockers_dynamic_ledger"},
    {"id": "E04", "group": "REPORTING_JOBS", "objective": "Test refs collected; statuses từ executed evidence", "mapped_test": "test_v12_2_acceptance.py::test_e04_manifest_refs_collected_and_executed"},
    {"id": "E05", "group": "REPORTING_JOBS", "objective": "XLSX types/null/datetime/formula injection/reopen", "mapped_test": "test_v12_2_acceptance.py::test_e05_xlsx_formula_injection_and_reopen"},
    {"id": "E06", "group": "REPORTING_JOBS", "objective": "Raw curve counts và timestamps <= cutoff; downsample giữ extrema nếu có", "mapped_test": "test_v12_2_acceptance.py::test_e06_equity_curve_fidelity"},
    {"id": "E07", "group": "REPORTING_JOBS", "objective": "Full stress giữ variant/dataset/config; spread/latency thực thay fill", "mapped_test": "test_v12_2_acceptance.py::test_e07_full_stress_test_fidelity"},
    {"id": "E08", "group": "REPORTING_JOBS", "objective": "Fixed-book repricing không double entry slip và có label riêng", "mapped_test": "test_v12_2_acceptance.py::test_e08_fixed_book_repricing_label"},
    {"id": "E09", "group": "REPORTING_JOBS", "objective": "Invalid DTO modes/ranges/risk/fee/hour/nonfinite trả 422 dễ hiểu", "mapped_test": "test_v12_2_acceptance.py::test_e09_invalid_dto_422_responses"},
    {"id": "E10", "group": "REPORTING_JOBS", "objective": "Legacy/new API contracts, frontend typecheck/build", "mapped_test": "test_v12_2_acceptance.py::test_e10_api_contract_backwards_compatibility"},
    {"id": "E11", "group": "REPORTING_JOBS", "objective": "Worker cancel/checkpoint/resume không double posting hoặc global mutation", "mapped_test": "test_v12_2_acceptance.py::test_e11_job_worker_cancel_and_isolation"},
    {"id": "E12", "group": "REPORTING_JOBS", "objective": "Health/WS responsiveness và download path traversal", "mapped_test": "test_v12_2_acceptance.py::test_e12_health_responsiveness_and_path_traversal"}
]


def build_v12_2_requirement_manifest(test_evidence_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Builds the authoritative 74-requirement manifest for V12_2.
    If test_evidence_map is provided (mapping requirement ID or test function name to status 'PASS'/'FAIL'/'BLOCKED'),
    the status is dynamically assigned from executed evidence.
    Otherwise, default status is 'NOT_RUN'.
    """
    evidence = test_evidence_map or {}
    results = []
    for req in ALL_74_REQUIREMENTS:
        req_id = req["id"]
        mapped_test = req["mapped_test"]
        test_func = mapped_test.split("::")[-1] if "::" in mapped_test else mapped_test
        status = evidence.get(req_id, evidence.get(test_func, evidence.get(mapped_test, "NOT_RUN")))
        results.append({
            "id": req_id,
            "group": req["group"],
            "objective": req["objective"],
            "command": mapped_test,
            "expected": f"Yêu cầu kỹ thuật {req_id}: đạt tiêu chuẩn kiểm định độc lập",
            "actual": f"Kiểm định qua fixture/suite {test_func}",
            "status": status
        })
    return results
