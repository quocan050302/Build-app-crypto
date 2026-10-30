"""
Aurum Desk V17.9.0 — Strategy Decision Contract.

Re-exports the immutable MarketDecisionContext, StrategyDecisionSnapshot,
DecisionStatus, SharedStrategyEvaluator, and parity verification primitives.
"""

from services.shared_strategy_evaluator import (
    ALL_EVALUATED_MODEL_IDS,
    PRODUCTION_MODEL_IDS,
    REQUIRED_PARITY_FIELDS,
    VALID_DECISION_STATUSES,
    DecisionStatus,
    MarketDecisionContext,
    SharedStrategyEvaluator,
    StrategyDecisionSnapshot,
    build_market_decision_context,
    characterize_production_baseline_decision,
    compare_decision_parity,
    verify_multi_adapter_parity,
    verify_no_synthetic_index_signals_in_source,
)

__all__ = [
    "ALL_EVALUATED_MODEL_IDS",
    "PRODUCTION_MODEL_IDS",
    "REQUIRED_PARITY_FIELDS",
    "VALID_DECISION_STATUSES",
    "DecisionStatus",
    "MarketDecisionContext",
    "SharedStrategyEvaluator",
    "StrategyDecisionSnapshot",
    "build_market_decision_context",
    "characterize_production_baseline_decision",
    "compare_decision_parity",
    "verify_multi_adapter_parity",
    "verify_no_synthetic_index_signals_in_source",
]
