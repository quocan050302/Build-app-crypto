/**
 * Client-side authoritative domain calculator mirroring backend/domain_calculator.py
 * Calculates Gross/Net Risk, Reward, Sizing, Fees, Leverage, Margin, and Liquidation.
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
  maxLeverage: number;
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
  maxLeverage: 50,
};

export interface BitgetTier {
  tier: number;
  maxNotional: number;
  maxLeverage: number;
  mmr: number;
  deduction: number;
}

export const BITGET_XAUUSDT_TIERS: BitgetTier[] = [
  { tier: 1, maxNotional: 50000.0, maxLeverage: 50, mmr: 0.005, deduction: 0.0 },
  { tier: 2, maxNotional: 100000.0, maxLeverage: 25, mmr: 0.010, deduction: 250.0 },
  { tier: 3, maxNotional: 200000.0, maxLeverage: 15, mmr: 0.015, deduction: 750.0 },
];

export function getTierInfo(notional: number): BitgetTier {
  for (const t of BITGET_XAUUSDT_TIERS) {
    if (notional <= t.maxNotional) return t;
  }
  return BITGET_XAUUSDT_TIERS[BITGET_XAUUSDT_TIERS.length - 1];
}

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
  notionalUsdt: number;
  feesTotalUsdt: number;
  slippageTotalUsdt: number;
  canExecute: boolean;
  skipReason?: string;

  // Leverage & Margin
  leverage: number;
  marginMode: 'ISOLATED' | 'CROSS';
  initialMarginUsdt: number;
  maintenanceMarginUsdt: number;
  estimatedLiquidation: number | null;
  slLpBufferUsdt: number | null;
  tier: number;
  maxTierLeverage: number;
}

export function validatePriceGeometry(
  direction: 'LONG' | 'SHORT',
  entry: number,
  sl: number,
  tp: number
): { isValid: boolean; invalidReason?: string } {
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp)) {
    return { isValid: false, invalidReason: 'Mức giá phải là số thực hợp lệ (không chấp nhận NaN hoặc Infinity)' };
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
    return { isValid: false, invalidReason: 'Khoảng cách dừng lỗ bằng 0 hoặc dưới 1 tick (0.01)' };
  }

  return { isValid: true };
}

export function calculateIsolatedLiquidation(
  direction: 'LONG' | 'SHORT',
  entry: number,
  quantity: number,
  leverage: number,
  multiplier: number = 1.0,
  takerFeeRate: number = 0.0004
): { lp: number; initialMargin: number; maintenanceMargin: number; tier: number; maxTierLeverage: number } {
  const notional = quantity * multiplier * entry;
  const tierInfo = getTierInfo(notional);
  const effLeverage = Math.min(leverage, tierInfo.maxLeverage);
  const initialMargin = effLeverage > 0 ? notional / effLeverage : notional;

  const qm = quantity * multiplier;
  const denomLong = qm * (1.0 - tierInfo.mmr - takerFeeRate);
  const denomShort = qm * (1.0 + tierInfo.mmr + takerFeeRate);

  let lp = 0;
  if (direction === 'LONG') {
    const num = entry * qm - initialMargin - tierInfo.deduction;
    lp = denomLong > 0 ? num / denomLong : 0;
  } else {
    const num = entry * qm + initialMargin + tierInfo.deduction;
    lp = denomShort > 0 ? num / denomShort : entry * 2.0;
  }

  const maintMargin = Math.max(0, notional * tierInfo.mmr - tierInfo.deduction);
  return {
    lp: Number(lp.toFixed(2)),
    initialMargin: Number(initialMargin.toFixed(2)),
    maintenanceMargin: Number(maintMargin.toFixed(2)),
    tier: tierInfo.tier,
    maxTierLeverage: tierInfo.maxLeverage,
  };
}

export function calculateClientRiskReward(
  direction: 'LONG' | 'SHORT',
  entry: number,
  sl: number,
  tp: number,
  capital: number = 1000.0,
  riskPct: number = 0.25,
  minNetRR: number = 2.0,
  quantityOverride?: number,
  leverage: number = 5,
  marginMode: 'ISOLATED' | 'CROSS' = 'ISOLATED',
  entryHasSlippage: boolean = false,
  minSlLpBufferUsdt: number = 1.0
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
      notionalUsdt: 0,
      feesTotalUsdt: 0,
      slippageTotalUsdt: 0,
      canExecute: false,
      skipReason: geo.invalidReason,
      leverage,
      marginMode,
      initialMarginUsdt: 0,
      maintenanceMarginUsdt: 0,
      estimatedLiquidation: null,
      slLpBufferUsdt: null,
      tier: 1,
      maxTierLeverage: 50,
    };
  }

  const mult = INSTRUMENT_METADATA.multiplier;
  const stopDistance = direction === 'LONG' ? entry - sl : sl - entry;
  const targetDistance = direction === 'LONG' ? tp - entry : entry - tp;

  const budgetUsdt = capital * (riskPct / 100.0);

  // Risk per unit
  const entryFeePerUnit = entry * INSTRUMENT_METADATA.takerFeeRate * mult;
  const slExitFeePerUnit = sl * INSTRUMENT_METADATA.takerFeeRate * mult;
  const slSlippagePerUnit = INSTRUMENT_METADATA.defaultSlippageUsd * mult;
  const entrySlippagePerUnit = entryHasSlippage ? 0 : INSTRUMENT_METADATA.defaultSlippageUsd * mult;

  const totalRiskPerUnit =
    stopDistance * mult +
    entryFeePerUnit +
    slExitFeePerUnit +
    entrySlippagePerUnit +
    slSlippagePerUnit;

  let canExecute = true;
  let skipReason: string | undefined = undefined;

  const rawQty =
    quantityOverride !== undefined && quantityOverride > 0
      ? quantityOverride
      : totalRiskPerUnit > 0
      ? budgetUsdt / totalRiskPerUnit
      : 0;

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
  // Assume taker fee for market triggered TP
  const tpExitFeeTotal = qty * mult * tp * INSTRUMENT_METADATA.takerFeeRate;

  // No double count if entryHasSlippage
  const entrySlippageTotal = entryHasSlippage ? 0 : qty * INSTRUMENT_METADATA.defaultSlippageUsd * mult;
  const slSlippageTotal = qty * INSTRUMENT_METADATA.defaultSlippageUsd * mult;

  const netRisk = grossLoss + entryFeeTotal + slExitFeeTotal + entrySlippageTotal + slSlippageTotal;
  const netReward = grossReward - entryFeeTotal - tpExitFeeTotal - entrySlippageTotal;

  // Quantity override budget check
  if (quantityOverride !== undefined && netRisk > budgetUsdt * 1.001) {
    canExecute = false;
    skipReason = `QTY_OVERRIDE_EXCEEDS_BUDGET: Khối lượng chỉ định ${qty} oz có rủi ro $${netRisk.toFixed(2)} vượt ngân sách rủi ro $${budgetUsdt.toFixed(2)}`;
  }

  const grossRR = grossLoss > 0 ? grossReward / grossLoss : 0;
  const netRR = netRisk > 0 ? netReward / netRisk : 0;

  // Strict threshold check, no rounding before check
  const meetsMinRR = netRR >= minNetRR;
  if (!meetsMinRR && canExecute) {
    canExecute = false;
    skipReason = `NET_RR_TOO_LOW: Net R:R 1:${netRR.toFixed(4)} chưa đạt ngưỡng tối thiểu 1:${minNetRR.toFixed(1)}`;
  }

  const effectiveRiskPct = capital > 0 ? (netRisk / capital) * 100.0 : 0;

  // Leverage & Liquidation
  const { lp, initialMargin, maintenanceMargin, tier, maxTierLeverage } = calculateIsolatedLiquidation(
    direction,
    entry,
    qty,
    leverage,
    mult,
    INSTRUMENT_METADATA.takerFeeRate
  );

  if (initialMargin > capital && canExecute) {
    canExecute = false;
    skipReason = `INSUFFICIENT_MARGIN: Ký quỹ yêu cầu $${initialMargin.toFixed(2)} vượt quá vốn khả dụng $${capital.toFixed(2)}`;
  }

  const slLpBuffer = direction === 'LONG' ? sl - lp : lp - sl;
  if (direction === 'LONG') {
    if (lp >= sl) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính (${lp.toFixed(2)}) nằm TRÊN hoặc BẰNG Stop Loss (${sl.toFixed(2)})`;
      }
    } else if (slLpBuffer < minSlLpBufferUsdt) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý ($${slLpBuffer.toFixed(2)}) nhỏ hơn tối thiểu $${minSlLpBufferUsdt.toFixed(2)}`;
      }
    }
  } else {
    if (lp <= sl) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính (${lp.toFixed(2)}) nằm DƯỚI hoặc BẰNG Stop Loss (${sl.toFixed(2)})`;
      }
    } else if (slLpBuffer < minSlLpBufferUsdt) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý ($${slLpBuffer.toFixed(2)}) nhỏ hơn tối thiểu $${minSlLpBufferUsdt.toFixed(2)}`;
      }
    }
  }

  if (marginMode === 'CROSS' && canExecute) {
    canExecute = false;
    skipReason = 'CROSS_MARGIN_UNSUPPORTED: Chế độ Cross margin chưa được hỗ trợ thực thi trên tài khoản paper';
  }

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
    notionalUsdt: Number(notional.toFixed(2)),
    feesTotalUsdt: Number((entryFeeTotal + slExitFeeTotal).toFixed(3)),
    slippageTotalUsdt: Number((entrySlippageTotal + slSlippageTotal).toFixed(3)),
    canExecute,
    skipReason,
    leverage,
    marginMode,
    initialMarginUsdt: initialMargin,
    maintenanceMarginUsdt: maintenanceMargin,
    estimatedLiquidation: lp,
    slLpBufferUsdt: Number(slLpBuffer.toFixed(2)),
    tier,
    maxTierLeverage,
  };
}
