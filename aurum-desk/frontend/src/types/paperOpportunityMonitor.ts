/**
 * frontend/src/types/paperOpportunityMonitor.ts
 * Aurum V17.0 — TypeScript types and contracts for Paper Opportunity Monitor.
 */

export interface MonitorChartTarget {
  kind: 'SETUP' | 'ORDER';
  id: string;
  setup_instance_id?: string | null;
  revision?: number | null;
  evidence_snapshot_id?: string | null;
  verified: boolean;
}

export interface ObservedMilestone {
  id: string;
  stage: string;
  stage_label_vi: string;
  first_recorded_at_ms: number | null;
  last_updated_at_ms: number | null;
  snapshot_as_of_ms: number | null;
  occurred_at_ms: number | null;
  reason_codes: string[];
  reason_summary_vi: string;
  source: 'FUNNEL_STAGE_SNAPSHOT' | 'COMMITTED_ORDER_MILESTONE' | 'TRACE';
  time_semantics: string;
  evidence_ids: string[];
  limitation?: string | null;
}

export interface OpportunityMonitorListItem {
  key: string;
  opportunity_id: string;
  run_id: string | null;
  model_id: string | null;
  direction: 'LONG' | 'SHORT' | 'UNKNOWN';
  session_id: string | null;
  session_label_vi: string | null;
  first_recorded_at_ms: number | null;
  last_observed_at_ms: number | null;
  current_stage: string;
  stage_label_vi: string;
  primary_reason_code: string | null;
  primary_reason_vi: string;
  classification: string | null;
  data_quality: 'VERIFIED' | 'PARTIAL' | 'UNVERIFIABLE';
  observed_milestone_count: number;
  score: number | null;
  score_source: string | null;
  zone_label: string | null;
  chart_target: MonitorChartTarget | null;
  limitations: string[];
  query_key?: string | null;
}

export interface OpportunityMonitorDetail {
  item: OpportunityMonitorListItem;
  milestones: ObservedMilestone[];
  plan?: {
    entry: number | null;
    stop_loss: number | null;
    take_profit: number | null;
    direction: string;
    price_source: string;
    as_of_ms?: number | null;
  } | null;
  order_outcomes: Array<{
    order_id: string;
    state: string;
    instrument: string;
    direction: string;
    planned_entry: number | null;
    actual_entry: number | null;
    stop_loss: number | null;
    take_profit: number | null;
    actual_exit: number | null;
    realized_pnl_net: number | null;
    gross_rr: number | null;
    estimated_net_rr: number | null;
    exit_cause: string | null;
    created_at: number;
    armed_at?: number | null;
    opened_at?: number | null;
    closed_at?: number | null;
    is_verified: boolean;
  }>;
  latency?: {
    trace_id?: string;
    detector_runtime_ms?: number | null;
    feed_delay_ms?: number | null;
    scheduler_delay_ms?: number | null;
    arm_delay_ms?: number | null;
    fill_delay_ms?: number | null;
    sample_count?: number;
    source?: string;
  } | null;
  classifier_result?: {
    classification?: string | null;
    terminal_stage?: string | null;
    miss_reason?: string | null;
    notes?: string;
  } | null;
  truncated: boolean;
  limitations: string[];
  query_key?: string | null;
}

export interface OpportunityMonitorSummary {
  schema_version: string;
  generated_at_ms: number;
  run_id?: string | null;
  query_scope: {
    symbol: string;
    scope: string;
    run_id?: string | null;
    model_id?: string | null;
    session_id?: string | null;
    start_ms?: number | null;
    end_ms?: number | null;
    limit?: number;
  };
  status: 'READY' | 'EMPTY' | 'NOT_CONFIGURED' | 'DEGRADED';
  counts: {
    total_unique_opportunities: number;
    zones_detected: number;
    zones_watched: number;
    zones_touched: number;
    trigger_confirmed: number;
    plans_valid: number;
    guard_blocked: number;
    armed: number;
    filled: number;
    expired: number;
    invalidated: number;
  };
  top_reasons: Array<{
    code: string;
    unique_opportunity_count: number;
    category: string;
    message_vi: string;
  }>;
  financial: {
    metric_scope: string;
    description_vi?: string;
    orders_count: number;
    closed_trades_count: number;
    verified_closed_count: number;
    wins_count: number;
    losses_count: number;
    breakevens_count: number;
    net_realized_pnl_usd: number | null;
    verified_net_pnl_subtotal_usd: number;
    winrate_pct: number | null;
    expectancy_usd_per_trade: number | null;
    accounting_status: 'COMPLETE' | 'PARTIAL' | 'UNVERIFIED';
  };
  coverage: {
    run_id: string | null;
    run_status: string;
    symbol: string;
    scope: string;
  };
  latency?: {
    sample_count: number;
    median_ms: number | null;
    p95_ms: number | null;
    source: string;
  } | null;
  limitations: string[];
  query_key?: string | null;
}

export interface MonitorListPage {
  items: OpportunityMonitorListItem[];
  total: number;
  has_more: boolean;
  next_cursor: string | null;
  snapshot_as_of_ms: number;
  limitations: string[];
  query_key?: string | null;
  run_id?: string | null;
}

export interface MonitorFilterState {
  period: 'today' | '7d' | 'custom';
  session: 'all' | 'asia' | 'london' | 'ny';
  status: 'all' | 'waiting' | 'blocked' | 'armed' | 'filled';
  customStartDate?: string;
  customEndDate?: string;
}

export interface BotWaitingSummary {
  headline: string;
  detail: string;
  tone: 'neutral' | 'info' | 'warning' | 'danger' | 'success';
  asOfMs: number | null;
  isHistoricalReference: boolean;
}

export interface MonitorQueryParams {
  symbol?: string;
  scope?: string;
  run_id?: string | null;
  model_id?: string | null;
  session_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  limit?: number;
  cursor?: string | null;
}

export interface ResolvedMonitorQuery {
  symbol: string;
  scope: string;
  run_id: string | null;
  model_id?: string | null;
  session_id?: string | null;
  start_ms: number | null;
  end_ms: number | null;
  now_ms?: number;
  query_key: string | null;
}

export type BoundMonitorQuery = Omit<
  ResolvedMonitorQuery,
  'run_id' | 'start_ms' | 'end_ms' | 'query_key'
> & {
  run_id: string;
  start_ms: number;
  end_ms: number;
  query_key: string;
};

export type MonitorResolution =
  | { status: 'RESOLVED'; query: BoundMonitorQuery }
  | { status: 'EMPTY'; query: BoundMonitorQuery }
  | { status: 'UNAVAILABLE'; reason: string; message_vi: string };

export type MonitorContextSnapshot =
  | { status: 'READY'; epoch: number; query: BoundMonitorQuery }
  | {
      status: 'CLOSED' | 'LOADING' | 'UNAVAILABLE' | 'ERROR';
      epoch: number;
      query: null;
      reason?: string;
    };

export interface MonitorRequestLease {
  readonly epoch: number;
  readonly query: Readonly<BoundMonitorQuery>;
}

export interface CanonicalPaperOrderDTO {
  id: string;
  setup_id?: string | null;
  setup_instance_id?: string | null;
  instrument: string;
  direction: 'LONG' | 'SHORT';
  state: string;
  timeframe?: string;
  order_type?: string;
  planned_entry: number;
  actual_entry?: number | null;
  stop_loss: number;
  take_profit: number;
  quantity?: number;
  initial_risk_usdt?: number;
  risk_pct?: number;
  gross_rr?: number;
  estimated_net_rr?: number;
  leverage?: number;
  margin_mode?: string;
  estimated_liquidation?: number | null;
  created_at?: number;
  armed_at?: number | null;
  opened_at?: number | null;
  closed_at?: number | null;
  cost_snapshot?: Record<string, any> | string | null;
  quality_snapshot_id?: string | null;
  evidence_snapshot_id?: string | null;
  scenario_snapshot_id?: string | null;
  candidate_score_summary?: Record<string, any> | null;
  realized_pnl_net?: number | null;
  realized_r?: number | null;
  exit_cause?: string | null;
}

export interface CanonicalWatchSetupDTO {
  id: string;
  setup_id?: string;
  setup_instance_id: string;
  version?: number;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  state: string;
  timeframe?: string;
  provisional_entry?: number;
  provisional_sl?: number;
  provisional_tp?: number;
  confirmed_entry?: number | null;
  confirmed_sl?: number | null;
  confirmed_tp?: number | null;
  planned_entry?: number;
  stop_loss?: number;
  take_profit?: number;
  created_at?: number;
  resolved_display_plan?: any;
}

export type MonitorChartFocusContext =
  | {
      status: 'AVAILABLE';
      kind: 'ORDER';
      query_key: string;
      opportunity_id: string;
      target_id: string;
      dto: CanonicalPaperOrderDTO;
    }
  | {
      status: 'AVAILABLE';
      kind: 'SETUP';
      query_key: string;
      opportunity_id: string;
      target_id: string;
      dto: CanonicalWatchSetupDTO;
    }
  | {
      status: 'UNAVAILABLE' | 'STALE_TARGET' | 'SCOPE_MISMATCH';
      query_key: string | null;
      opportunity_id: string;
      reason_code: string;
      message_vi: string;
    };
