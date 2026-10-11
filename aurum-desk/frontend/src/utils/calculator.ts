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
  maxLeverage: 100,
};

export interface BitgetTier {
  tier: number;
  maxNotional: number;
  maxLeverage: number;
  mmr: number;
  deduction: number;
}

export const BITGET_XAUUSDT_TIERS: BitgetTier[] = [
  { tier: 1, maxNotional: 20000.0, maxLeverage: 100, mmr: 0.005, deduction: 0.0 },
  { tier: 2, maxNotional: 200000.0, maxLeverage: 75, mmr: 0.010, deduction: 0.0 },
  { tier: 3, maxNotional: 500000.0, maxLeverage: 50, mmr: 0.015, deduction: 0.0 },
  { tier: 4, maxNotional: 2000000.0, maxLeverage: 25, mmr: 0.020, deduction: 0.0 },
  { tier: 5, maxNotional: 5000000.0, maxLeverage: 20, mmr: 0.025, deduction: 0.0 },
  { tier: 6, maxNotional: 20000000.0, maxLeverage: 10, mmr: 0.050, deduction: 0.0 },
  { tier: 7, maxNotional: 40000000.0, maxLeverage: 5, mmr: 0.100, deduction: 0.0 },
  { tier: 8, maxNotional: 60000000.0, maxLeverage: 4, mmr: 0.125, deduction: 0.0 },
  { tier: 9, maxNotional: 100000000.0, maxLeverage: 2, mmr: 0.300, deduction: 0.0 },
  { tier: 10, maxNotional: 200000000.0, maxLeverage: 1, mmr: 0.600, deduction: 0.0 },
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
  direction: 'LONG' | 'SHORT' | string;
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

  // Breakdown fields
  entryFeeUsdt: number;
  slExitFeeUsdt: number;
  tpExitFeeUsdt: number;
  entrySlippageUsdt: number;
  slExitSlippageUsdt: number;
  tpExitSlippageUsdt: number;
  multiplier: number;
  blockerList: string[];

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
  direction: 'LONG' | 'SHORT' | string,
  entry: number,
  sl: number,
  tp: number
): { isValid: boolean; invalidReason?: string } {
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp)) {
    return { isValid: false, invalidReason: 'PRICES_NOT_FINITE: Mức giá phải là số thực hợp lệ (không chấp nhận NaN hoặc Infinity)' };
  }
  if (entry <= 0 || sl <= 0 || tp <= 0) {
    return { isValid: false, invalidReason: 'PRICES_MUST_BE_POSITIVE: Mọi mức giá phải lớn hơn 0' };
  }

  if (direction === 'LONG') {
    if (!(sl < entry && entry < tp)) {
      return {
        isValid: false,
        invalidReason: `INVALID_LONG_GEOMETRY: Yêu cầu SL (${sl.toFixed(2)}) < Entry (${entry.toFixed(2)}) < TP (${tp.toFixed(2)})`,
      };
    }
  } else if (direction === 'SHORT') {
    if (!(tp < entry && entry < sl)) {
      return {
        isValid: false,
        invalidReason: `INVALID_SHORT_GEOMETRY: Yêu cầu TP (${tp.toFixed(2)}) < Entry (${entry.toFixed(2)}) < SL (${sl.toFixed(2)})`,
      };
    }
  } else {
    return {
      isValid: false,
      invalidReason: `UNKNOWN_DIRECTION: ${direction}`,
    };
  }

  if (Math.abs(entry - sl) < 0.01) {
    return { isValid: false, invalidReason: 'STOP_DISTANCE_ZERO_OR_TOO_TIGHT: Khoảng cách dừng lỗ bằng 0 hoặc dưới 1 tick (0.01)' };
  }

  return { isValid: true };
}

export function calculateIsolatedLiquidation(
  direction: 'LONG' | 'SHORT' | string,
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

export interface ClientCostOverrides {
  makerFeeRate?: number;
  takerFeeRate?: number;
  defaultSlippageUsd?: number;
  tpSlippageUsd?: number;
  multiplier?: number;
  tpIsMaker?: boolean;
}

export function calculateClientRiskReward(
  direction: 'LONG' | 'SHORT' | string,
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
  minSlLpBufferUsdt: number = 1.0,
  costs?: ClientCostOverrides
): ClientCalcResult {
  const mult = costs?.multiplier && costs.multiplier > 0 ? costs.multiplier : INSTRUMENT_METADATA.multiplier;
  const makerRate = costs?.makerFeeRate ?? INSTRUMENT_METADATA.makerFeeRate;
  const takerRate = costs?.takerFeeRate ?? INSTRUMENT_METADATA.takerFeeRate;
  const slipUsd = costs?.defaultSlippageUsd ?? INSTRUMENT_METADATA.defaultSlippageUsd;
  const tpSlipUsd = costs?.tpSlippageUsd ?? 0.0;
  const tpIsMaker = costs?.tpIsMaker ?? false;

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
      entryFeeUsdt: 0,
      slExitFeeUsdt: 0,
      tpExitFeeUsdt: 0,
      entrySlippageUsdt: 0,
      slExitSlippageUsdt: 0,
      tpExitSlippageUsdt: 0,
      multiplier: mult,
      blockerList: geo.invalidReason ? [geo.invalidReason] : [],
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

  // Check quantityOverride validity
  if (quantityOverride !== undefined && (!Number.isFinite(quantityOverride) || quantityOverride <= 0)) {
    const invalidQtyErr = 'INVALID_QUANTITY: Khối lượng chỉ định phải là số dương hữu hạn';
    return {
      isValid: false,
      invalidReason: invalidQtyErr,
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
      skipReason: invalidQtyErr,
      entryFeeUsdt: 0,
      slExitFeeUsdt: 0,
      tpExitFeeUsdt: 0,
      entrySlippageUsdt: 0,
      slExitSlippageUsdt: 0,
      tpExitSlippageUsdt: 0,
      multiplier: mult,
      blockerList: [invalidQtyErr],
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

  const stopDistance = direction === 'LONG' ? entry - sl : sl - entry;
  const targetDistance = direction === 'LONG' ? tp - entry : entry - tp;

  const budgetUsdt = capital * (riskPct / 100.0);

  // Risk per unit
  const entryFeePerUnit = entry * takerRate * mult;
  const slExitFeePerUnit = sl * takerRate * mult;
  const slSlippagePerUnit = slipUsd * mult;
  const entrySlippagePerUnit = entryHasSlippage ? 0 : slipUsd * mult;

  const totalRiskPerUnit =
    stopDistance * mult +
    entryFeePerUnit +
    slExitFeePerUnit +
    entrySlippagePerUnit +
    slSlippagePerUnit;

  let canExecute = true;
  let skipReason: string | undefined = undefined;
  const blockers: string[] = [];

  const rawQty =
    quantityOverride !== undefined && quantityOverride > 0
      ? quantityOverride
      : totalRiskPerUnit > 0
      ? budgetUsdt / totalRiskPerUnit
      : 0;

  const step = INSTRUMENT_METADATA.qtyStep;
  // Exact step floor rounding
  let qty = Math.floor(Math.round((rawQty / step) * 1e8) / 1e8) * step;
  qty = Number(qty.toFixed(4));

  const minQty = INSTRUMENT_METADATA.minQty;
  if (qty < minQty) {
    if (quantityOverride !== undefined) {
      canExecute = false;
      skipReason = `MIN_QTY_NOT_MET: Khối lượng chỉ định ${qty} nhỏ hơn tối thiểu ${minQty}`;
      blockers.push('MIN_QTY_NOT_MET');
    } else {
      const minUnitRisk = minQty * totalRiskPerUnit;
      if (minUnitRisk > budgetUsdt) {
        canExecute = false;
        skipReason = `MIN_QTY_EXCEEDS_BUDGET: Khối lượng tối thiểu ${minQty} oz có rủi ro $${minUnitRisk.toFixed(2)} vượt ngân sách rủi ro $${budgetUsdt.toFixed(2)}`;
        blockers.push('MIN_QTY_EXCEEDS_BUDGET');
        qty = minQty;
      } else {
        qty = minQty;
      }
    }
  }

  const notional = qty * mult * entry;
  if (notional < INSTRUMENT_METADATA.minNotional) {
    canExecute = false;
    skipReason = `MIN_NOTIONAL_NOT_MET: Giá trị lệnh $${notional.toFixed(2)} nhỏ hơn mức tối thiểu $${INSTRUMENT_METADATA.minNotional.toFixed(2)}`;
    blockers.push('MIN_NOTIONAL_NOT_MET');
  }

  const grossLoss = qty * mult * stopDistance;
  const grossReward = qty * mult * targetDistance;

  const entryFeeTotal = qty * mult * entry * takerRate;
  const slExitFeeTotal = qty * mult * sl * takerRate;
  const tpFeeRate = tpIsMaker ? makerRate : takerRate;
  const tpExitFeeTotal = qty * mult * tp * tpFeeRate;

  // Slippage
  const entrySlippageTotal = entryHasSlippage ? 0 : qty * slipUsd * mult;
  const slSlippageTotal = qty * slipUsd * mult;
  const tpSlippageTotal = tpIsMaker ? 0 : qty * tpSlipUsd * mult;

  const netRisk = grossLoss + entryFeeTotal + slExitFeeTotal + entrySlippageTotal + slSlippageTotal;
  const netReward = grossReward - entryFeeTotal - tpExitFeeTotal - entrySlippageTotal - tpSlippageTotal;

  // Quantity override budget check
  if (quantityOverride !== undefined && netRisk > budgetUsdt * 1.001) {
    canExecute = false;
    skipReason = `QTY_OVERRIDE_EXCEEDS_BUDGET: Khối lượng chỉ định ${qty} oz có rủi ro $${netRisk.toFixed(2)} vượt ngân sách rủi ro $${budgetUsdt.toFixed(2)}`;
    blockers.push('QTY_OVERRIDE_EXCEEDS_BUDGET');
  }

  const grossRR = grossLoss > 0 ? grossReward / grossLoss : 0;
  const netRR = netRisk > 0 ? netReward / netRisk : 0;

  // Guard: non-positive net reward
  if (netReward <= 0 && canExecute) {
    canExecute = false;
    skipReason = `NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến ($${netReward.toFixed(2)}) nhỏ hơn hoặc bằng 0 sau chi phí`;
    blockers.push('NET_REWARD_NON_POSITIVE');
  }

  // Strict unrounded threshold check
  const meetsMinRR = netRR >= minNetRR;
  if (!meetsMinRR && canExecute) {
    canExecute = false;
    skipReason = `NET_RR_TOO_LOW: Net R:R 1:${netRR.toFixed(4)} chưa đạt ngưỡng tối thiểu 1:${minNetRR.toFixed(1)}`;
    blockers.push('NET_RR_TOO_LOW');
  }

  const effectiveRiskPct = capital > 0 ? (netRisk / capital) * 100.0 : 0;

  // Leverage & Liquidation
  const { lp, initialMargin, maintenanceMargin, tier, maxTierLeverage } = calculateIsolatedLiquidation(
    direction,
    entry,
    qty,
    leverage,
    mult,
    takerRate
  );

  if (initialMargin > capital && canExecute) {
    canExecute = false;
    skipReason = `INSUFFICIENT_MARGIN: Ký quỹ yêu cầu $${initialMargin.toFixed(2)} vượt quá vốn khả dụng $${capital.toFixed(2)}`;
    blockers.push('INSUFFICIENT_MARGIN');
  }

  const slLpBuffer = direction === 'LONG' ? sl - lp : lp - sl;
  if (direction === 'LONG') {
    if (lp >= sl) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính (${lp.toFixed(2)}) nằm TRÊN hoặc BẰNG Stop Loss (${sl.toFixed(2)})`;
        blockers.push('LIQUIDATION_BEFORE_SL');
      }
    } else if (slLpBuffer < minSlLpBufferUsdt) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý ($${slLpBuffer.toFixed(2)}) nhỏ hơn tối thiểu $${minSlLpBufferUsdt.toFixed(2)}`;
        blockers.push('LIQUIDATION_BUFFER_TOO_TIGHT');
      }
    }
  } else {
    if (lp <= sl) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BEFORE_SL: Giá thanh lý ước tính (${lp.toFixed(2)}) nằm DƯỚI hoặc BẰNG Stop Loss (${sl.toFixed(2)})`;
        blockers.push('LIQUIDATION_BEFORE_SL');
      }
    } else if (slLpBuffer < minSlLpBufferUsdt) {
      if (canExecute) {
        canExecute = false;
        skipReason = `LIQUIDATION_BUFFER_TOO_TIGHT: Khoảng đệm SL-Thanh lý ($${slLpBuffer.toFixed(2)}) nhỏ hơn tối thiểu $${minSlLpBufferUsdt.toFixed(2)}`;
        blockers.push('LIQUIDATION_BUFFER_TOO_TIGHT');
      }
    }
  }

  if (marginMode === 'CROSS' && canExecute) {
    canExecute = false;
    skipReason = 'CROSS_MARGIN_UNSUPPORTED: Chế độ Cross margin chưa được hỗ trợ thực thi trên tài khoản paper';
    blockers.push('CROSS_MARGIN_UNSUPPORTED');
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
    entryFeeUsdt: Number(entryFeeTotal.toFixed(4)),
    slExitFeeUsdt: Number(slExitFeeTotal.toFixed(4)),
    tpExitFeeUsdt: Number(tpExitFeeTotal.toFixed(4)),
    entrySlippageUsdt: Number(entrySlippageTotal.toFixed(4)),
    slExitSlippageUsdt: Number(slSlippageTotal.toFixed(4)),
    tpExitSlippageUsdt: Number(tpSlippageTotal.toFixed(4)),
    multiplier: mult,
    blockerList: blockers,
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

export interface CalculateRiskRewardOptions {
  direction: 'LONG' | 'SHORT' | string;
  entry: number;
  sl: number;
  tp: number;
  capital?: number;
  riskPct?: number;
  minNetRR?: number;
  quantityOverride?: number;
  leverage?: number;
  marginMode?: 'ISOLATED' | 'CROSS';
  entryHasSlippage?: boolean;
  minSlLpBufferUsdt?: number;
  multiplier?: number;
  costs?: ClientCostOverrides;
}

export function calculateRiskReward(
  optionsOrDirection: CalculateRiskRewardOptions | ('LONG' | 'SHORT' | string),
  entry?: number,
  sl?: number,
  tp?: number,
  capital?: number,
  riskPct?: number,
  minNetRR?: number,
  quantityOverride?: number,
  leverage?: number,
  marginMode?: 'ISOLATED' | 'CROSS',
  entryHasSlippage?: boolean,
  minSlLpBufferUsdt?: number,
  costs?: ClientCostOverrides
): ClientCalcResult {
  if (typeof optionsOrDirection === 'object' && optionsOrDirection !== null) {
    const opts = optionsOrDirection as CalculateRiskRewardOptions;
    const mergedCosts: ClientCostOverrides = {
      ...(opts.costs || {}),
      ...(opts.multiplier !== undefined ? { multiplier: opts.multiplier } : {})
    };
    return calculateClientRiskReward(
      opts.direction,
      opts.entry,
      opts.sl,
      opts.tp,
      opts.capital ?? 1000.0,
      opts.riskPct ?? 0.25,
      opts.minNetRR ?? 2.0,
      opts.quantityOverride,
      opts.leverage ?? 5,
      opts.marginMode ?? 'ISOLATED',
      opts.entryHasSlippage ?? false,
      opts.minSlLpBufferUsdt ?? 1.0,
      mergedCosts
    );
  }
  return calculateClientRiskReward(
    optionsOrDirection,
    entry!,
    sl!,
    tp!,
    capital ?? 1000.0,
    riskPct ?? 0.25,
    minNetRR ?? 2.0,
    quantityOverride,
    leverage ?? 5,
    marginMode ?? 'ISOLATED',
    entryHasSlippage ?? false,
    minSlLpBufferUsdt ?? 1.0,
    costs
  );
}

