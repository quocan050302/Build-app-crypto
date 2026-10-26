/** V14.4.1 — cached Asia/Europe/NY forecasts (read-only API: GET never runs the engine). */

export type SessionKey = 'ASIA' | 'LONDON' | 'NY';

export interface SessionHypothesis {
  direction: 'LONG' | 'SHORT' | null;
  thesis_vi: string;
  conditions_vi: string[];
  invalidation_vi: string | null;
}

export interface SessionLiquidityBrief {
  id: string;
  price: number;
  label_vi: string;
  state: string;
  side: string;
  source_kind: string;
  is_partial: boolean;
  known_at_ms: number | null;
}

export interface SessionProbability {
  status: string;
  p_tp_before_sl?: number | null;
  p_session_event?: number | null;
  event?: string | null;
  horizon?: string | null;
  support_n?: number | null;
  model_version?: string | null;
  oos_from_ms?: number | null;
  oos_to_ms?: number | null;
  uncertainty?: Record<string, unknown> | null;
  note?: string;
  note_vi?: string;
}

export interface SessionForecastView {
  session: SessionKey;
  session_label_vi: string;
  session_id: string;
  as_of_ms: number;
  data_status: 'OK' | 'PARTIAL' | 'NOT_STARTED' | 'DATA_UNAVAILABLE' | string;
  is_partial: boolean;
  hypothesis_type: string;
  facts: string[];
  primary: SessionHypothesis | null;
  alternative: SessionHypothesis | null;
  watched_liquidity: SessionLiquidityBrief[];
  target: { id?: string; price?: number; label_vi?: string } | null;
  confirmation: string[];
  invalidation: string[];
  summary_vi: string;
  probability: SessionProbability;
  entry_eligible: boolean;
  entry_note_vi: string;
}

export interface SessionForecastsResponse {
  symbol: string;
  status: 'OK' | 'NOT_READY' | 'NOT_CACHED' | 'ERROR' | string;
  day?: string | null;
  as_of_ms?: number | null;
  forecasts: Partial<Record<SessionKey, SessionForecastView>>;
  coverage: Record<string, unknown>;
  data_issues: unknown[];
}

export const SESSION_CHIP_LABEL: Record<SessionKey, string> = {
  ASIA: 'Á',
  LONDON: 'Âu',
  NY: 'Mỹ',
};

/**
 * Default session to show: the session containing "now" in the app's research convention
 * (America/New_York: Asia 19:00-03:00, Europe 03:00-08:00 before NY opens, NY from 08:00); otherwise NY.
 */
export function defaultSessionKey(nowMs: number): SessionKey {
  const hour = Number(
    new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }).format(new Date(nowMs))
  ) % 24;
  if (hour >= 19 || hour < 3) return 'ASIA';
  if (hour >= 3 && hour < 8) return 'LONDON';
  return 'NY';
}
