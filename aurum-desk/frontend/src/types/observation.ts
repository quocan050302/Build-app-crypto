/**
 * frontend/src/types/observation.ts
 * AURUM V17.6 — Autonomous Observation & Decision Intelligence Types.
 */

export interface EngineHealthSnapshot {
  engine_instance_id: string;
  symbol: string;
  run_id?: string | null;
  state: 'RUNNING' | 'IDLE' | 'WAITING_MARKET' | 'PAUSED' | 'DEGRADED' | 'STALLED' | 'STOPPED' | 'UNKNOWN';
  is_stalled: boolean;
  stalled_reason?: string | null;
  last_process_heartbeat_at_ms?: number | null;
  last_heartbeat_age_sec?: number | null;
  last_evaluation_started_at_ms?: number | null;
  last_evaluation_completed_at_ms?: number | null;
  last_successful_evaluation_at_ms?: number | null;
  last_evaluation_age_sec?: number | null;
  last_cycle_id?: string | null;
  cycles_count: number;
  failed_cycles_count: number;
  cadence_sec: number;
  now_ms: number;
}

export interface TimeframeCandleHealth {
  latest_closed_candle_ts?: number | null;
  candle_age_sec?: number | null;
  expected_interval_ms: number;
  is_stale: boolean;
  freshness_state: 'FRESH' | 'STALE';
}

export interface MarketFeedHealthSnapshot {
  symbol: string;
  transport_state: 'CONNECTED' | 'DEGRADED' | 'DISCONNECTED' | 'CONNECTING' | string;
  feed_source: string;
  freshness_state: 'CONNECTED_FRESH' | 'CONNECTED_STALE' | 'DISCONNECTED' | 'RECONNECTING' | 'UNKNOWN';
  is_fresh: boolean;
  is_weekend_break: boolean;
  contract_schedule?: string | null;
  contract_operational_state?: string | null;
  underlying_reference_state?: string | null;
  strategy_session_state?: string | null;
  quote_freshness_state?: string | null;
  last_quote_at_ms?: number | null;
  quote_age_sec?: number | null;
  bid?: number | null;
  ask?: number | null;
  mid?: number | null;
  timeframe_health: Record<string, TimeframeCandleHealth>;
  has_gap: boolean;
  reconnect_count: number;
  last_success_time: number;
  now_ms: number;
}

export interface ObservationCoverage {
  total_span_hours: number;
  observed_healthy_hours: number;
  known_downtime_hours: number;
  coverage_pct?: number | null;
  coverage_state: 'FULL' | 'PARTIAL' | 'LOW' | 'UNKNOWN' | 'RUN_NOT_FOUND' | 'MARKET_CLOSED' | 'NO_ELIGIBLE_WINDOW' | 'UNVERIFIABLE';
  evidence_type: 'LIVE_PAPER_FORWARD_OBSERVATION' | 'SIMULATED_SOAK';
  coverage_policy_version?: string | null;
  schedule_source?: string | null;
}

export interface WaitingConditionItem {
  id: string;
  name_vi: string;
  status: 'FULFILLED' | 'WAITING' | 'BLOCKED' | 'FAILED' | 'UNVERIFIABLE';
  detail: string;
}

export interface WaitingSetupItem {
  opportunity_id: string;
  model_id: string;
  direction: 'LONG' | 'SHORT';
  stage: string;
  stage_title_vi: string;
  occurred_at_ms: number;
  conditions: WaitingConditionItem[];
}

export interface BlockedCategoryItem {
  category: string;
  category_title_vi: string;
  count: number;
}

export interface ObservationFinancialSummary {
  status?: string | null;
  run_id?: string | null;
  closed_trades?: number | null;
  open_trades?: number | null;
  armed_orders?: number | null;
  wins?: number | null;
  losses?: number | null;
  breakevens?: number | null;
  winrate_pct?: number | null;
  net_pnl_usd?: number | null;
  net_expectancy?: number | null;
  max_drawdown_usd?: number | null;
  max_drawdown_pct?: number | null;
  initial_capital_usdt?: number | null;
  current_equity_usdt?: number | null;
}

export interface DecisionEventItem {
  event_id: string;
  opportunity_id: string;
  model_id: string;
  direction: 'LONG' | 'SHORT';
  stage: string;
  stage_title_vi: string;
  primary_reason_code: string;
  reason_category: string;
  reason_category_title_vi: string;
  short_message_vi: string;
  technical_message: string;
  occurred_at_ms: number;
  order_id?: string | null;
  setup_id?: string | null;
}

export interface DecisionsListPage {
  items: DecisionEventItem[];
  limit: number;
  has_more: boolean;
  next_cursor?: string | null;
}

export interface ObservationDashboardSnapshot {
  schema_version: string;
  symbol: string;
  scope: string;
  run_id?: string | null;
  window: {
    start_ms: number;
    end_ms: number;
    now_ms: number;
  };
  engine: EngineHealthSnapshot;
  feed: MarketFeedHealthSnapshot;
  coverage: ObservationCoverage;
  funnel: any;
  decisions: DecisionsListPage;
  waiting_setups: WaitingSetupItem[];
  blocked_categories: BlockedCategoryItem[];
  financial: ObservationFinancialSummary;
  incidents: any[];
  latest_cycle?: any;
}
