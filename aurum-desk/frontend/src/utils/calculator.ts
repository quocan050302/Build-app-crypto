/**
 * Client-side authoritative domain calculator mirroring backend/domain_calculator.py
 * Calculates Gross/Net Risk, Reward, Sizing, Fees, and Strict Geometry.
 */

export interface InstrumentMetadata {
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  multiplier: number;
  tickSize: number;
  qtyStep: number;
  minQty: number;
  minNotional: number;
  makerFeeRate: number;
  takerFeeRate: number;
  defaultSlippageUsd: number;
}

export const INSTRUMENT_METADATA: InstrumentMetadata = {
  symbol: 'XAUUSDT',
  baseAsset: 'XAU',
  quoteAsset: 'USDT',
  multiplier: 1.0,
  tickSize: 0.01,
  qtyStep: 0.01,
  minQty: 0.01,
  minNotional: 5.0,
  makerFeeRate: 0.0002,
  takerFeeRate: 0.0004,
  defaultSlippageUsd: 0.10,
};

export interface ClientCalcResult {
  isValid: boolean;
  invalidReason?: string;
  direction: 'LONG' | 'SHORT';
  plannedEntry: number;
  stopLoss: number;
  takeProfit: number;
  stopDistance: number;
  targetDistance: number;
  quantity: number;
  budgetUsdt: number;
  grossLossUsdt: number;
  grossRewardUsdt: number;
  netRiskUsdt: number;
  netRewardUsdt: number;
  grossRR: number;
  estimatedNetRR: number;
  meetsMinRR: boolean;
  effectiveRiskPct: number;
  canExecute: boolean;
  skipReason?: string;
}

export function validatePriceGeometry(
  direction: 'LONG' | 'SHORT',
  entry: number,
  sl: number,
  tp: number
): { isValid: boolean; invalidReason?: string } {
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp)) {
    return { isValid: false, invalidReason: 'Giá không hợp lệ (NaN hoặc vô hạn)' };
  }
  if (entry <= 0 || sl <= 0 || tp <= 0) {
    return { isValid: false, invalidReason: 'Mọi mức giá phải lớn hơn 0' };
  }

  if (direction === 'LONG') {
    if (!(sl < entry && entry < tp)) {
      return {
        isValid: false,
        invalidReason: `Sai thứ tự giá LONG: Yêu cầu SL (${sl.toFixed(2)}) < Entry (${entry.toFixed(2)}) < TP (${tp.toFixed(2)})`,
      };
    }
  } else if (direction === 'SHORT') {
    if (!(tp < entry && entry < sl)) {
      return {
        isValid: false,
        invalidReason: `Sai thứ tự giá SHORT: Yêu cầu TP (${tp.toFixed(2)}) < Entry (${entry.toFixed(2)}) < SL (${sl.toFixed(2)})`,
      };
    }
  }

  if (Math.abs(entry - sl) < 0.01) {
    return { isValid: false, invalidReason: 'Khoảng cách cắt lỗ bằng 0 hoặc quá hẹp' };
  }

  return { isValid: true };
}

export function calculateClientRiskReward(
  direction: 'LONG' | 'SHORT',
  entry: number,
  sl: number,
  tp: number,
  capital: number = 1000.0,
  riskPct: number = 0.25,
  minNetRR: number = 2.0,
  quantityOverride?: number
): ClientCalcResult {
  const geo = validatePriceGeometry(direction, entry, sl, tp);
  if (!geo.isValid) {
    return {
      isValid: false,
      invalidReason: geo.invalidReason,
      direction,
      plannedEntry: entry,
      stopLoss: sl,
      takeProfit: tp,
      stopDistance: 0,
      targetDistance: 0,
      quantity: 0,
      budgetUsdt: 0,
      grossLossUsdt: 0,
      grossRewardUsdt: 0,
      netRiskUsdt: 0,
      netRewardUsdt: 0,
      grossRR: 0,
      estimatedNetRR: 0,
      meetsMinRR: false,
      effectiveRiskPct: 0,
      canExecute: false,
      skipReason: geo.invalidReason,
    };
  }

  const mult = INSTRUMENT_METADATA.multiplier;
  const stopDistance = direction === 'LONG' ? entry - sl : sl - entry;
  const targetDistance = direction === 'LONG' ? tp - entry : entry - tp;

  const budgetUsdt = capital * (riskPct / 100.0);

  // Risk per unit
  const entryFeePerUnit = entry * INSTRUMENT_METADATA.takerFeeRate * mult;
  const slExitFeePerUnit = sl * INSTRUMENT_METADATA.takerFeeRate * mult;
  const entrySlippagePerUnit = INSTRUMENT_METADATA.defaultSlippageUsd * mult;
  const slSlippagePerUnit = INSTRUMENT_METADATA.defaultSlippageUsd * mult;

  const totalRiskPerUnit =
    stopDistance * mult +
    entryFeePerUnit +
    slExitFeePerUnit +
    entrySlippagePerUnit +
    slSlippagePerUnit;

  let canExecute = true;
  let skipReason: string | undefined = undefined;

  let rawQty = quantityOverride !== undefined && quantityOverride > 0
    ? quantityOverride
    : budgetUsdt / totalRiskPerUnit;

  const step = INSTRUMENT_METADATA.qtyStep;
  let qty = Math.floor(rawQty / step) * step;
  qty = Number(qty.toFixed(4));

  const minQty = INSTRUMENT_METADATA.minQty;
  if (qty < minQty) {
    const minUnitRisk = minQty * totalRiskPerUnit;
    if (minUnitRisk > budgetUsdt) {
      canExecute = false;
      skipReason = `MIN_QTY_EXCEEDS_BUDGET: Khối lượng tối thiểu ${minQty} oz có rủi ro $${minUnitRisk.toFixed(2)} vượt ngân sách rủi ro $${budgetUsdt.toFixed(2)}`;
      qty = minQty;
    } else {
      qty = minQty;
    }
  }

  const notional = qty * mult * entry;
  if (notional < INSTRUMENT_METADATA.minNotional) {
    canExecute = false;
    skipReason = `MIN_NOTIONAL_NOT_MET: Giá trị lệnh $${notional.toFixed(2)} nhỏ hơn mức tối thiểu $${INSTRUMENT_METADATA.minNotional.toFixed(2)}`;
  }

  const grossLoss = qty * mult * stopDistance;
  const grossReward = qty * mult * targetDistance;

  const entryFeeTotal = qty * mult * entry * INSTRUMENT_METADATA.takerFeeRate;
  const slExitFeeTotal = qty * mult * sl * INSTRUMENT_METADATA.takerFeeRate;
  const tpExitFeeTotal = qty * mult * tp * INSTRUMENT_METADATA.makerFeeRate;
  const entrySlippageTotal = qty * INSTRUMENT_METADATA.defaultSlippageUsd * mult;
  const slSlippageTotal = qty * INSTRUMENT_METADATA.defaultSlippageUsd * mult;

  const netRisk = grossLoss + entryFeeTotal + slExitFeeTotal + entrySlippageTotal + slSlippageTotal;
  const netReward = grossReward - entryFeeTotal - tpExitFeeTotal - entrySlippageTotal;

  const grossRR = grossLoss > 0 ? grossReward / grossLoss : 0;
  const netRR = netRisk > 0 ? netReward / netRisk : 0;

  // Strict threshold check, no rounding before check
  const meetsMinRR = netRR >= minNetRR;
  if (!meetsMinRR && canExecute) {
    canExecute = false;
    skipReason = `NET_RR_TOO_LOW: Net R:R 1:${netRR.toFixed(4)} chưa đạt ngưỡng tối thiểu 1:${minNetRR.toFixed(1)}`;
  }

  const effectiveRiskPct = capital > 0 ? (netRisk / capital) * 100.0 : 0;

  return {
    isValid: true,
    direction,
    plannedEntry: Number(entry.toFixed(2)),
    stopLoss: Number(sl.toFixed(2)),
    takeProfit: Number(tp.toFixed(2)),
    stopDistance: Number(stopDistance.toFixed(2)),
    targetDistance: Number(targetDistance.toFixed(2)),
    quantity: qty,
    budgetUsdt: Number(budgetUsdt.toFixed(2)),
    grossLossUsdt: Number(grossLoss.toFixed(2)),
    grossRewardUsdt: Number(grossReward.toFixed(2)),
    netRiskUsdt: Number(netRisk.toFixed(2)),
    netRewardUsdt: Number(netReward.toFixed(2)),
    grossRR: Number(grossRR.toFixed(4)),
    estimatedNetRR: Number(netRR.toFixed(4)),
    meetsMinRR,
    effectiveRiskPct: Number(effectiveRiskPct.toFixed(4)),
    canExecute,
    skipReason,
  };
}
