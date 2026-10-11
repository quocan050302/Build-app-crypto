/**
 * frontend/src/types/setupQuality.ts
 * V14 — Frontend Types for SMC Setup Quality and Structural Evidence.
 */

export type QualityStatus =
  | 'SATISFIED'
  | 'PARTIAL'
  | 'INSUFFICIENT_DATA'
  | 'BLOCKED_EXISTING'
  | 'ERROR'
  | 'NOT_ASSESSED';

export type CheckStatus = 'MET' | 'NOT_MET' | 'UNKNOWN' | 'NOT_APPLICABLE';
export type CheckImportance = 'REQUIRED' | 'SUPPORTING';

export interface QualityCheck {
  code: string;
  status: CheckStatus;
  importance: CheckImportance;
  explanation_vi: string;
  observed?: string;
  expected?: string;
  evidence_ids?: string[];
  not_applicable_reason?: string;
  measured_at_ms?: number;
}

export interface QualitySummary {
  status: QualityStatus;
  required_met: number;
  required_total: number;
  score_pct: number;
  headline_vi: string;
  next_condition_vi: string;
  caution_vi?: string | null;
}

export interface EvidenceItem {
  id: string;
  kind: 'SWING_HIGH' | 'SWING_LOW' | 'SWEEP' | 'BOS' | 'CHOCH' | 'DISPLACEMENT' | 'FVG' | 'LIQUIDITY_TARGET';
  symbol: string;
  timeframe: string;
  known_at_ms: number;
  level?: number;
  direction?: 'LONG' | 'SHORT' | 'BULLISH' | 'BEARISH';
  source_ids?: string[];
  detail?: string;
  extra?: Record<string, any>;
}

export interface QualityAssessment {
  status: QualityStatus;
  checks: QualityCheck[];
  summary: QualitySummary;
  related_evidence_ids: string[];
  missing_evidence_ids: string[];
  cautions: string[];
  mode: 'EXPLAIN_ONLY';
}

export interface QualitySnapshot {
  schema_version: 'v14.0';
  snapshot_id: string;
  setup_id?: string;
  setup_instance_id?: string;
  setup_revision: number;
  quality_revision: number;
  symbol: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT';
  strategy_family: string;
  as_of_ms: number;
  plan_fingerprint: string;
  plan_snapshot: {
    direction: 'LONG' | 'SHORT';
    entry_price: number;
    stop_loss: number;
    take_profit: number;
    quantity: number;
    leverage: number;
    margin_mode: string;
    net_rr: number;
    risk_usdt: number;
  };
  assessment: QualityAssessment;
  evidence: EvidenceItem[];
  evidence_digest?: string;
}

export interface SelectedSetupContext {
  selectionKey: string;
  source: 'WATCH_SETUP' | 'DRAFT' | 'TRADE' | 'SIGNAL';
  setupId?: string;
  setupInstanceId?: string;
  revision?: number;
  planFingerprint: string;
  direction: 'LONG' | 'SHORT';
  plannedEntry: number;
  stopLoss: number;
  takeProfit: number;
  quantity?: number;
  netRR?: number;
  snapshot?: QualitySnapshot | null;
  loading: boolean;
  error?: string | null;
  isDraft: boolean;
  isStale?: boolean;
}
