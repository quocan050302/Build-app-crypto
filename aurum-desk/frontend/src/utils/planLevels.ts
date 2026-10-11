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
