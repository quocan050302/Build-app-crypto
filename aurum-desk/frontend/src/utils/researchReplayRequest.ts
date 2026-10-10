import type { ReplayRunRequest, TradingPolicy } from '../api/client';

export interface ResearchReplayFormState {
  startDate: string;
  endDate: string;
  strategyVariant: string;
  entryCadence: 'CONFIRMED_ONLY' | 'DAILY_PAPER';
  initialCapital: number;
  leverage: number;
  maxRiskPct: number;
  quotaRiskPct?: number;
  nyMaxFills?: number;
  dailyMinFillsTarget?: number;
  scheduledDeadlineHour?: number;
  scheduledDeadlineMinute?: number;
  dateBasis?: string;
}

export function buildResearchReplayRequest(
  form: ResearchReplayFormState,
  policy?: TradingPolicy | null
): ReplayRunRequest {
  const maxFills = Math.min(3, Math.max(1, form.nyMaxFills ?? policy?.max_daily_fills ?? 3));
  const minTarget = Math.min(maxFills, Math.max(1, form.dailyMinFillsTarget ?? 1));

  return {
    run_name: `eval_${form.strategyVariant.toLowerCase()}_${form.entryCadence.toLowerCase()}_${form.startDate}_${form.endDate}`,
    symbol: 'XAUUSDT',
    start_date: form.startDate,
    end_date: form.endDate,
    initial_equity: form.initialCapital > 0 ? form.initialCapital : 1000.0,
    leverage: Math.min(125, Math.max(1, form.leverage || 30)),
    risk_pct: form.maxRiskPct > 0 ? form.maxRiskPct : 0.25,
    max_risk_pct: form.maxRiskPct > 0 ? form.maxRiskPct : 0.25,
    quota_risk_pct: form.quotaRiskPct ?? 0.10,
    selected_session: 'NEW_YORK',
    strategy_variant: form.strategyVariant,
    entry_cadence: form.entryCadence,
    ny_max_fills: maxFills,
    daily_min_fills_target: minTarget,
    scheduled_deadline_hour: form.scheduledDeadlineHour ?? 14,
    scheduled_deadline_minute: form.scheduledDeadlineMinute ?? 30,
    date_basis: form.dateBasis ?? 'VN_DATE',
    scheduler_policy_version: 'v13.3',
    include_5m: true,
    use_5m_driver: true
  };
}

export function computeRequestFingerprint(req: ReplayRunRequest): string {
  const payload = {
    symbol: req.symbol,
    start_date: req.start_date,
    end_date: req.end_date,
    initial_equity: req.initial_equity,
    leverage: req.leverage,
    risk_pct: req.risk_pct,
    quota_risk_pct: req.quota_risk_pct,
    strategy_variant: req.strategy_variant,
    entry_cadence: req.entry_cadence,
    ny_max_fills: req.ny_max_fills,
    daily_min_fills_target: req.daily_min_fills_target,
    scheduled_deadline_hour: req.scheduled_deadline_hour,
    scheduled_deadline_minute: req.scheduled_deadline_minute
  };
  return JSON.stringify(payload);
}
