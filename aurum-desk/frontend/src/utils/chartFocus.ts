export interface SelectionFocusRequest {
  selectionKey: string;
  startMs: number;
  endMs: number;
  essentialPrices: number[];
  mode: 'FIT_SELECTION' | 'LIVE';
}

export function buildSelectionFocusRequest(
  selectionKey: string,
  events: any[],
  plan?: any,
  mode: 'FIT_SELECTION' | 'LIVE' = 'FIT_SELECTION'
): SelectionFocusRequest | null {
  if (!events.length && !plan) return null;

  const times: number[] = [];
  const prices: number[] = [];

  for (const ev of events) {
    if (Number.isSafeInteger(ev.occurred_at_ms) && ev.occurred_at_ms > 0) {
      times.push(ev.occurred_at_ms);
    }
    if (typeof ev.price === 'number' && Number.isFinite(ev.price)) {
      prices.push(ev.price);
    }
  }

  if (plan) {
    if (typeof plan.planned_entry === 'number') prices.push(plan.planned_entry);
    if (typeof plan.actual_entry === 'number') prices.push(plan.actual_entry);
    if (typeof plan.stop_loss === 'number') prices.push(plan.stop_loss);
    if (typeof plan.take_profit === 'number') prices.push(plan.take_profit);
    if (typeof plan.invalidation_price === 'number') prices.push(plan.invalidation_price);
    if (plan.entry_time && plan.entry_time > 0) {
      times.push(plan.entry_time * 1000);
    }
  }

  if (!times.length) {
    return null;
  }

  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);

  // Add finite padding: 30 minutes before first event, 60 minutes after last event
  const startMs = minTime - 30 * 60 * 1000;
  const endMs = maxTime + 60 * 60 * 1000;

  return {
    selectionKey,
    startMs,
    endMs,
    essentialPrices: Array.from(new Set(prices)),
    mode,
  };
}
