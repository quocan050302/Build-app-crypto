import { validatePriceGeometry } from './calculator';

export interface SetupPriceLevels {
  direction?: unknown;
  state?: string;
  confirmed_entry?: number | null;
  confirmed_sl?: number | null;
  confirmed_tp?: number | null;
  provisional_entry?: number | null;
  provisional_sl?: number | null;
  provisional_tp?: number | null;
  resolved_display_plan?: ResolvedDisplayPlan;
}

export type AtomicPlan = {
  direction: 'LONG' | 'SHORT';
  entry: number;
  stopLoss: number;
  takeProfit: number;
  levelSource: 'confirmed' | 'provisional';
};

export type PlanResolution =
  | { isValid: true; plan: AtomicPlan }
  | { isValid: false; reason: string };

/** Mirror the existing backend plan_resolver contract, without hybrid price triples. */
export function resolveSetupPriceLevels(setup: SetupPriceLevels): PlanResolution {
  if (setup.direction !== 'LONG' && setup.direction !== 'SHORT') {
    return { isValid: false, reason: 'Thiếu hướng LONG/SHORT hợp lệ của setup.' };
  }
  const confirmed = [setup.confirmed_entry, setup.confirmed_sl, setup.confirmed_tp];
  const confirmedCount = confirmed.filter(value => value != null).length;
  if (confirmedCount > 0 && confirmedCount < 3) {
    return { isValid: false, reason: 'Bộ giá xác nhận chưa đầy đủ; không trộn Entry/SL/TP với giá dự kiến.' };
  }
  const makePlan = (values: Array<number | null | undefined>, levelSource: AtomicPlan['levelSource']): PlanResolution => {
    const [entry, stopLoss, takeProfit] = values;
    if (entry == null || stopLoss == null || takeProfit == null) {
      return { isValid: false, reason: 'Chưa có đủ Entry, SL và TP cho kế hoạch này.' };
    }
    const geometry = validatePriceGeometry(setup.direction as 'LONG' | 'SHORT', entry, stopLoss, takeProfit);
    if (!geometry.isValid) return { isValid: false, reason: geometry.invalidReason ?? 'Bộ giá không hợp lệ.' };
    return { isValid: true, plan: { direction: setup.direction as 'LONG' | 'SHORT', entry, stopLoss, takeProfit, levelSource } };
  };
  if (confirmedCount === 3) {
    const plan = makePlan(confirmed, 'confirmed');
    if (plan.isValid || setup.state === 'ARMED' || setup.state === 'PAPER_OPEN') return plan;
    // Backend allows an unarmed, stale confirmed triple to fall back as a whole.
  }
  return makePlan([setup.provisional_entry, setup.provisional_sl, setup.provisional_tp], 'provisional');
}

export function formatRR(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `1:${value.toFixed(2)}` : '—';
}

export interface ResolvedDisplayPlan {
  identity: {
    setup_id?: string;
    instance_id?: string;
    revision?: number;
    timeframe?: string;
  };
  status: string;
  is_valid: boolean;
  reason_code: string;
  reason_message: string;
  direction: 'LONG' | 'SHORT';
  level_source: string;
  entry: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  as_of_ms?: number;
  price_basis?: string;
  target_source_id?: string;
  target_cutoff_ms?: number;
  financial: {
    gross_rr: number;
    net_rr: number;
    net_risk_usdt: number;
    quantity: number;
    leverage: number;
    margin_mode: string;
    estimated_liquidation: number | null;
    meets_min_rr: boolean;
    gross_loss_usdt?: number | null;
    gross_reward_usdt?: number | null;
    net_reward_usdt?: number | null;
    entry_fee_usdt?: number | null;
    sl_exit_fee_usdt?: number | null;
    tp_exit_fee_usdt?: number | null;
    entry_slippage_usdt?: number | null;
    sl_exit_slippage_usdt?: number | null;
    tp_exit_slippage_usdt?: number | null;
  } | null;
  diagnostic_prices?: Record<string, number | null>;
}

export function formatKnownNumber(value: unknown, decimals: number): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(decimals)
    : 'Chưa xác định';
}

import type { RiskRewardData } from '../plugins/RiskRewardPrimitive';

export function getCanonicalSetupPlan(setup: any): PlanResolution {
  if (setup?.resolved_display_plan) {
    const rdp = setup.resolved_display_plan;
    if (rdp.is_valid && rdp.entry != null && rdp.stop_loss != null && rdp.take_profit != null) {
      return {
        isValid: true,
        plan: {
          direction: rdp.direction,
          entry: rdp.entry,
          stopLoss: rdp.stop_loss,
          takeProfit: rdp.take_profit,
          levelSource: (rdp.level_source as any) || 'provisional',
        },
      };
    }
    if (!rdp.is_valid) {
      return {
        isValid: false,
        reason: rdp.reason_message || 'Kế hoạch không hợp lệ hoặc thiếu mục tiêu thanh khoản.',
      };
    }
  }
  return resolveSetupPriceLevels(setup);
}

export function planToOverlay(
  setup: any,
  planRes?: PlanResolution,
  options?: { isDraft?: boolean; draftId?: string }
): RiskRewardData {
  const rdp = setup?.resolved_display_plan;
  const isDraft = options?.isDraft ?? false;
  const overlayId = isDraft ? (options?.draftId || `draft-${Date.now()}`) : (setup?.id || `setup-${Date.now()}`);

  let entry = 0;
  let sl = 0;
  let tp = 0;
  let direction: 'LONG' | 'SHORT' = 'SHORT';

  if (rdp && rdp.entry != null && rdp.stop_loss != null && rdp.take_profit != null) {
    entry = rdp.entry;
    sl = rdp.stop_loss;
    tp = rdp.take_profit;
    direction = rdp.direction;
  } else if (planRes && planRes.isValid) {
    entry = planRes.plan.entry;
    sl = planRes.plan.stopLoss;
    tp = planRes.plan.takeProfit;
    direction = planRes.plan.direction;
  } else {
    const res = resolveSetupPriceLevels(setup);
    if (res.isValid) {
      entry = res.plan.entry;
      sl = res.plan.stopLoss;
      tp = res.plan.takeProfit;
      direction = res.plan.direction;
    }
  }

  const fin = rdp?.financial;
  const state = isDraft ? 'draft' : (setup?.state === 'ARMED' ? 'armed' : 'candidate');

  return {
    id: overlayId,
    direction: direction,
    state: state,
    plannedEntry: entry,
    stopLoss: sl,
    takeProfit: tp,
    quantity: fin?.quantity ?? (setup?.quantity || 0),
    initialRiskUsdt: fin?.net_risk_usdt ?? (setup?.risk_usdt || 0),
    grossRewardUsdt: fin?.gross_reward_usdt,
    grossLossUsdt: fin?.gross_loss_usdt,
    netRewardUsdt: fin?.net_reward_usdt,
    multiplier: 1.0,
    entryFeeUsdt: fin?.entry_fee_usdt ?? undefined,
    slExitFeeUsdt: fin?.sl_exit_fee_usdt ?? undefined,
    tpExitFeeUsdt: fin?.tp_exit_fee_usdt ?? undefined,
    entrySlippageUsdt: fin?.entry_slippage_usdt ?? undefined,
    slExitSlippageUsdt: fin?.sl_exit_slippage_usdt ?? undefined,
    tpExitSlippageUsdt: fin?.tp_exit_slippage_usdt ?? undefined,
    riskPct: setup?.risk_pct ?? 0.25,
    grossRR: fin?.gross_rr ?? (setup?.gross_rr || 0),
    estimatedNetRR: fin?.net_rr ?? (setup?.net_rr || 0),
    isValid: rdp ? rdp.is_valid : (entry > 0 && sl > 0 && tp > 0),
    invalidReason: rdp ? (!rdp.is_valid ? rdp.reason_message : undefined) : undefined,
    leverage: fin?.leverage ?? setup?.leverage ?? 20,
    marginMode: (fin?.margin_mode as any) ?? setup?.margin_mode ?? 'ISOLATED',
    estimatedLiquidation: fin?.estimated_liquidation ?? setup?.estimated_liquidation,
    readOnly: !isDraft,
  };
}
