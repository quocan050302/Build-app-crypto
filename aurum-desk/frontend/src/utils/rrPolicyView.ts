/**
 * frontend/src/utils/rrPolicyView.ts
 * Aurum V16.39.18 — Unified RR Policy View & Order Snapshot Adapter
 *
 * Responsibilities:
 * - Normalize server responses from /api/scalp/config and /api/v1/policy/trading.
 * - Resolve immutable order RR threshold from PaperOrder / Trade DTOs (raw snake_case)
 *   and SelectedTradeIntent (mapped camelCase).
 * - Strict tri-state / quadri-state classification: KNOWN | MISSING | INVALID | UNKNOWN.
 * - Full-precision decision comparison in compareNetRr (never round diff before meetsMin).
 * - Never fabricate 2.0 or current policy for missing historical order thresholds.
 */

import { calculateClientRiskReward } from './calculator';

export type RrView =
  | { status: 'KNOWN'; minNetRR: number; source: string; fingerprint?: string }
  | { status: 'MISSING'; minNetRR: null; source?: string; reason?: string }
  | { status: 'INVALID'; minNetRR: null; source?: string; reason?: string };

export interface NormalizedRrPolicyResponse {
  status: 'READY' | 'INVALID';
  value: number | null;
  source: string;
  fingerprint?: string;
  error?: string;
}

/**
 * Validates and normalizes server response from GET / POST scalp config or trading policy.
 * Only returns status: 'READY' when the value is a finite number >= 1.20.
 */
export function normalizeRrPolicyResponse(raw: any): NormalizedRrPolicyResponse {
  if (!raw || typeof raw !== 'object') {
    return {
      status: 'INVALID',
      value: null,
      source: 'UNKNOWN',
      error: 'Phản hồi cấu hình R:R rỗng hoặc không đúng định dạng',
    };
  }

  const eff = raw.effective_config && typeof raw.effective_config === 'object' ? raw.effective_config : null;
  const rawVal = eff?.min_net_rr ?? eff?.effective_min_net_rr ?? raw.min_net_rr ?? raw.effective_min_net_rr;

  if (rawVal === undefined || rawVal === null || typeof rawVal === 'boolean') {
    return {
      status: 'INVALID',
      value: null,
      source: 'UNKNOWN',
      error: 'Thiếu trường min_net_rr trong phản hồi máy chủ',
    };
  }

  const num = Number(rawVal);
  if (!Number.isFinite(num) || num < 1.5) {
    return {
      status: 'INVALID',
      value: null,
      source: 'UNKNOWN',
      error: `Ngưỡng Min Net R:R (${rawVal}) không hợp lệ (yêu cầu >= 1.50R)`,
    };
  }

  const source =
    (typeof raw.rr_policy_source === 'string' && raw.rr_policy_source) ||
    (typeof eff?.rr_policy_source === 'string' && eff.rr_policy_source) ||
    'SYSTEM_CONFIG';

  const fingerprint =
    (typeof raw.rr_policy_fingerprint === 'string' && raw.rr_policy_fingerprint) ||
    (typeof eff?.rr_policy_fingerprint === 'string' && eff.rr_policy_fingerprint) ||
    undefined;

  return {
    status: 'READY',
    value: Number(num.toFixed(4)),
    source,
    fingerprint,
  };
}

/**
 * Safely parses JSON strings into objects without throwing exceptions.
 */
function safeJsonParse(raw: unknown): Record<string, any> | null {
  if (!raw) return null;
  if (typeof raw === 'object') return raw as Record<string, any>;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return typeof parsed === 'object' && parsed !== null ? parsed : null;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Extracts and resolves the immutable historical RR threshold from a raw order or trade DTO.
 * Input contract: Raw snake_case DTO (cost_snapshot, arm_decision_snapshot, min_net_rr_at_arm, financial).
 *
 * Rules:
 * - Preserves historical valid > 0 (does not force 1.5 floor on old orders).
 * - Never returns current policy or fake 2.0 when missing.
 */
export function resolveOrderRrView(orderDto: any): RrView {
  if (!orderDto || typeof orderDto !== 'object') {
    return { status: 'MISSING', minNetRR: null, source: 'UNKNOWN_HISTORICAL' };
  }

  // 1. Check financial sub-object
  if (orderDto.financial && typeof orderDto.financial === 'object') {
    const fin = orderDto.financial;
    const finMin = fin.min_net_rr;
    const finSource = fin.rr_policy_source || 'ORDER_SNAPSHOT';
    const finFp = fin.rr_policy_fingerprint;
    const compliance = fin.rr_compliance_status;

    if (compliance === 'INVALID') {
      return { status: 'INVALID', minNetRR: null, source: finSource, reason: 'Historical snapshot threshold is invalid' };
    }

    if (finMin != null && typeof finMin !== 'boolean') {
      const val = Number(finMin);
      if (Number.isFinite(val) && val > 0) {
        return { status: 'KNOWN', minNetRR: val, source: finSource, fingerprint: finFp };
      }
      return { status: 'INVALID', minNetRR: null, source: finSource, reason: 'Invalid non-positive threshold in financial' };
    }

    if (compliance === 'UNKNOWN' || finMin === null) {
      return { status: 'MISSING', minNetRR: null, source: finSource || 'UNKNOWN_HISTORICAL' };
    }
  }

  // 2. Check cost_snapshot or arm_decision_snapshot
  const costSnap = safeJsonParse(orderDto.cost_snapshot);
  const armSnap = safeJsonParse(orderDto.arm_decision_snapshot);

  let rawVal: unknown = undefined;
  let hasKey = false;
  let source: string = 'ORDER_SNAPSHOT';
  let fingerprint: string | undefined = undefined;

  const inspectObj = (obj: Record<string, any> | null) => {
    if (!obj || hasKey) return;
    if ('min_net_rr_at_arm' in obj) {
      hasKey = true;
      rawVal = obj.min_net_rr_at_arm;
      if (obj.rr_policy_source) source = obj.rr_policy_source;
      if (obj.rr_policy_fingerprint) fingerprint = obj.rr_policy_fingerprint;
    } else if ('min_net_rr' in obj) {
      hasKey = true;
      rawVal = obj.min_net_rr;
      if (obj.rr_policy_source) source = obj.rr_policy_source;
      if (obj.rr_policy_fingerprint) fingerprint = obj.rr_policy_fingerprint;
    }
  };

  inspectObj(costSnap);
  inspectObj(armSnap);

  if (!hasKey) {
    if ('min_net_rr_at_arm' in orderDto) {
      hasKey = true;
      rawVal = orderDto.min_net_rr_at_arm;
    } else if ('min_net_rr' in orderDto) {
      hasKey = true;
      rawVal = orderDto.min_net_rr;
    }
    if (orderDto.rr_policy_source) source = orderDto.rr_policy_source;
    if (orderDto.rr_policy_fingerprint) fingerprint = orderDto.rr_policy_fingerprint;
  }

  if (!hasKey || rawVal === null || rawVal === undefined) {
    return { status: 'MISSING', minNetRR: null, source: 'UNKNOWN_HISTORICAL' };
  }

  if (typeof rawVal === 'boolean') {
    return { status: 'INVALID', minNetRR: null, source, reason: 'Boolean is not a valid RR threshold' };
  }

  const parsedNum = Number(rawVal);
  if (Number.isFinite(parsedNum) && parsedNum > 0) {
    return { status: 'KNOWN', minNetRR: parsedNum, source, fingerprint };
  }

  return { status: 'INVALID', minNetRR: null, source, reason: `Value ${String(rawVal)} is not a valid positive number` };
}

/**
 * Convenient extractor for RiskRewardData and SelectedTradeIntent.
 * Carries forward explicit KNOWN, MISSING, or INVALID status.
 */
export function getOrderRrFields(orderDto: any): {
  minNetRR?: number;
  rrPolicySource?: string;
  rrPolicyFingerprint?: string;
  rrPolicyStatus?: 'KNOWN' | 'MISSING' | 'INVALID';
  rrPolicyReason?: string;
} {
  const view = resolveOrderRrView(orderDto);
  if (view.status === 'KNOWN') {
    return {
      minNetRR: view.minNetRR,
      rrPolicySource: view.source,
      rrPolicyFingerprint: view.fingerprint,
      rrPolicyStatus: 'KNOWN',
    };
  }
  if (view.status === 'INVALID') {
    return {
      minNetRR: undefined,
      rrPolicySource: view.source ?? undefined,
      rrPolicyFingerprint: undefined,
      rrPolicyStatus: 'INVALID',
      rrPolicyReason: view.reason,
    };
  }
  return {
    minNetRR: undefined,
    rrPolicySource: view.source ?? undefined,
    rrPolicyFingerprint: undefined,
    rrPolicyStatus: 'MISSING',
    rrPolicyReason: view.reason,
  };
}

/**
 * Resolves RR view from a mapped SelectedTradeIntent or overlay projection.
 * Distinguishes KNOWN, MISSING, and INVALID without re-parsing raw snake_case snapshots
 * when mapped fields are already present.
 */
export function resolveMappedOrderRrView(mapped: any): RrView {
  if (!mapped || typeof mapped !== 'object') {
    return { status: 'MISSING', minNetRR: null, source: 'UNKNOWN_HISTORICAL' };
  }

  // Precedence 1: If raw snapshot fields are present (e.g. raw DTO passed), use authoritative raw parser
  if (mapped.cost_snapshot || mapped.arm_decision_snapshot || mapped.financial || mapped.min_net_rr_at_arm) {
    return resolveOrderRrView(mapped);
  }

  // Precedence 2: Explicit mapped status
  if (mapped.rrPolicyStatus === 'INVALID') {
    return {
      status: 'INVALID',
      minNetRR: null,
      source: mapped.rrPolicySource || 'ORDER_SNAPSHOT',
      reason: mapped.rrPolicyReason || 'Historical snapshot threshold is invalid',
    };
  }

  if (mapped.rrPolicyStatus === 'MISSING') {
    return {
      status: 'MISSING',
      minNetRR: null,
      source: mapped.rrPolicySource || 'UNKNOWN_HISTORICAL',
      reason: mapped.rrPolicyReason,
    };
  }

  // Precedence 3: Mapped camelCase minNetRR
  const rawVal = mapped.minNetRR;
  if (rawVal !== undefined && rawVal !== null) {
    if (typeof rawVal === 'boolean') {
      return {
        status: 'INVALID',
        minNetRR: null,
        source: mapped.rrPolicySource || 'ORDER_SNAPSHOT',
        reason: 'Boolean is not a valid RR threshold',
      };
    }
    const num = Number(rawVal);
    if (Number.isFinite(num) && num > 0) {
      return {
        status: 'KNOWN',
        minNetRR: num,
        source: mapped.rrPolicySource || 'ORDER_SNAPSHOT',
        fingerprint: mapped.rrPolicyFingerprint,
      };
    }
    return {
      status: 'INVALID',
      minNetRR: null,
      source: mapped.rrPolicySource || 'ORDER_SNAPSHOT',
      reason: `Value ${String(rawVal)} is not a valid positive number`,
    };
  }

  return {
    status: 'MISSING',
    minNetRR: null,
    source: mapped.rrPolicySource || 'UNKNOWN_HISTORICAL',
  };
}

// ============================================================================
// Aurum V16.39.18 — Unified Selection & Comparison Helpers
// ============================================================================

export type SelectionRrPolicyKind = 'CURRENT' | 'ORDER_SNAPSHOT' | 'UNKNOWN' | 'INVALID';

export type SelectionRrPolicyView =
  | {
      kind: 'CURRENT';
      status: 'READY' | 'LOADING' | 'ERROR';
      minNetRR: number | null;
      source: string;
      fingerprint?: string;
      isAuthoritative: boolean;
    }
  | {
      kind: 'ORDER_SNAPSHOT';
      status: 'KNOWN' | 'MISSING' | 'INVALID';
      minNetRR: number | null;
      source: string;
      fingerprint?: string;
      orderId?: string;
    }
  | {
      kind: 'UNKNOWN';
      status: 'MISSING';
      minNetRR: null;
      source: string;
      reason?: string;
    }
  | {
      kind: 'INVALID';
      status: 'INVALID';
      minNetRR: null;
      source: string;
      reason?: string;
    };

export interface CurrentPolicyContext {
  value: number | null;
  source?: string;
  fingerprint?: string;
  status?: string;
  isConfirmed?: boolean;
}

/**
 * Resolves whether the currently inspected trade intent is a CURRENT evaluation or
 * a FROZEN historical order snapshot.
 *
 * Rules:
 * - intent.source === 'OPEN_POSITION' -> strictly inspects immutable order snapshot fields.
 * - intent.source in ['DRAFT', 'LIVE_CANDIDATE', 'WATCH_SETUP', 'SCENARIO'] -> current evaluation,
 *   bound to current verified Settings policy.
 * - Missing current policy never guesses 2.0 or 1.5; sets status to LOADING or MISSING.
 */
export function resolveSelectionRrPolicy(
  intent: any | null | undefined,
  currentPolicy?: CurrentPolicyContext | null
): SelectionRrPolicyView {
  const isExplicitlyUnconfirmed = Boolean(
    !currentPolicy ||
    currentPolicy.value == null ||
    !Number.isFinite(currentPolicy.value) ||
    currentPolicy.value < 1.0 ||
    currentPolicy.isConfirmed === false ||
    currentPolicy.status === 'loading' ||
    currentPolicy.status === 'LOADING' ||
    currentPolicy.source === 'UNCONFIRMED'
  );
  const isCurrentConfirmed = !isExplicitlyUnconfirmed;

  if (!intent) {
    if (isCurrentConfirmed) {
      return {
        kind: 'CURRENT',
        status: 'READY',
        minNetRR: currentPolicy!.value,
        source: currentPolicy!.source || 'SYSTEM_CONFIG',
        fingerprint: currentPolicy!.fingerprint,
        isAuthoritative: true,
      };
    }
    if (currentPolicy?.status === 'loading' || currentPolicy?.status === 'LOADING') {
      return {
        kind: 'CURRENT',
        status: 'LOADING',
        minNetRR: null,
        source: currentPolicy?.source || 'UNCONFIRMED',
        isAuthoritative: false,
      };
    }
    return {
      kind: 'UNKNOWN',
      status: 'MISSING',
      minNetRR: null,
      source: currentPolicy?.source || 'UNCONFIRMED',
      reason: 'Chưa có cấu hình chính sách R:R',
    };
  }

  // 1. FROZEN ORDER SNAPSHOT (source === 'OPEN_POSITION' or canonical order identification)
  if (intent.source === 'OPEN_POSITION' || (intent.order_id && intent.source !== 'DRAFT')) {
    const orderView = resolveMappedOrderRrView(intent);
    if (orderView.status === 'KNOWN') {
      return {
        kind: 'ORDER_SNAPSHOT',
        status: 'KNOWN',
        minNetRR: orderView.minNetRR,
        source: orderView.source,
        fingerprint: orderView.fingerprint,
        orderId: intent.order_id || intent.id,
      };
    }
    if (orderView.status === 'INVALID') {
      return {
        kind: 'ORDER_SNAPSHOT',
        status: 'INVALID',
        minNetRR: null,
        source: orderView.source || 'ORDER_SNAPSHOT',
        fingerprint: undefined,
        orderId: intent.order_id || intent.id,
      };
    }
    return {
      kind: 'ORDER_SNAPSHOT',
      status: 'MISSING',
      minNetRR: null,
      source: orderView.source || 'UNKNOWN_HISTORICAL',
      fingerprint: undefined,
      orderId: intent.order_id || intent.id,
    };
  }

  // 2. CURRENT EVALUATION (DRAFT, LIVE_CANDIDATE, WATCH_SETUP, SCENARIO)
  if (isCurrentConfirmed) {
    return {
      kind: 'CURRENT',
      status: 'READY',
      minNetRR: currentPolicy!.value,
      source: currentPolicy!.source || intent.rrPolicySource || 'SYSTEM_CONFIG',
      fingerprint: currentPolicy!.fingerprint || intent.rrPolicyFingerprint,
      isAuthoritative: currentPolicy?.isConfirmed !== false,
    };
  }

  if (currentPolicy?.status === 'loading' || currentPolicy?.status === 'LOADING' || currentPolicy?.isConfirmed === false || currentPolicy?.value == null) {
    return {
      kind: 'CURRENT',
      status: 'LOADING',
      minNetRR: null,
      source: currentPolicy?.source || 'UNCONFIRMED',
      isAuthoritative: false,
    };
  }

  return {
    kind: 'UNKNOWN',
    status: 'MISSING',
    minNetRR: null,
    source: currentPolicy?.source || 'UNKNOWN_CURRENT',
    reason: 'Chưa xác nhận cấu hình R:R hiện hành',
  };
}

export type NetRrComparisonStatus = 'MET' | 'NOT_MET' | 'UNKNOWN' | 'INVALID';

export interface NetRrComparisonResult {
  status: NetRrComparisonStatus;
  actualNetRR: number | null;
  requiredMinNetRR: number | null;
  diff: number | null;
  policyKind: SelectionRrPolicyKind;
  source?: string;
  label: string;
  meetsMin: boolean;
}

/**
 * Compares an actual Net R:R against the resolved selection policy view.
 * Performs direct full-precision comparison without rounding diff before decision.
 */
export function compareNetRr(
  actualNetRR: number | null | undefined,
  policyView: SelectionRrPolicyView
): NetRrComparisonResult {
  const actual = typeof actualNetRR === 'number' && Number.isFinite(actualNetRR) ? actualNetRR : null;
  const kind = policyView.kind;
  const minRR = policyView.minNetRR;

  if (actual === null || !Number.isFinite(actual)) {
    return {
      status: 'INVALID',
      actualNetRR: null,
      requiredMinNetRR: minRR,
      diff: null,
      policyKind: kind,
      source: 'source' in policyView ? policyView.source : undefined,
      label: 'R:R không hợp lệ',
      meetsMin: false,
    };
  }

  // If order snapshot threshold is missing or invalid
  if (kind === 'ORDER_SNAPSHOT') {
    if (policyView.status === 'MISSING' || minRR === null) {
      return {
        status: 'UNKNOWN',
        actualNetRR: actual,
        requiredMinNetRR: null,
        diff: null,
        policyKind: 'ORDER_SNAPSHOT',
        source: policyView.source || 'UNKNOWN_HISTORICAL',
        label: 'Chưa lưu ngưỡng lúc ARM',
        meetsMin: false,
      };
    }
    if (policyView.status === 'INVALID') {
      return {
        status: 'INVALID',
        actualNetRR: actual,
        requiredMinNetRR: null,
        diff: null,
        policyKind: 'ORDER_SNAPSHOT',
        source: policyView.source || 'ORDER_SNAPSHOT',
        label: 'Ngưỡng snapshot lúc ARM không hợp lệ',
        meetsMin: false,
      };
    }
  }

  // If current policy is unconfirmed or loading
  if (kind === 'CURRENT') {
    if (policyView.status === 'LOADING' || !policyView.isAuthoritative || minRR === null) {
      return {
        status: 'UNKNOWN',
        actualNetRR: actual,
        requiredMinNetRR: null,
        diff: null,
        policyKind: 'CURRENT',
        source: policyView.source || 'UNCONFIRMED',
        label: policyView.status === 'LOADING' ? 'Đang tải cấu hình R:R...' : 'Chưa xác nhận cấu hình R:R',
        meetsMin: false,
      };
    }
    if (policyView.status === 'ERROR') {
      return {
        status: 'UNKNOWN',
        actualNetRR: actual,
        requiredMinNetRR: null,
        diff: null,
        policyKind: 'CURRENT',
        source: policyView.source || 'UNCONFIRMED',
        label: 'Lỗi cấu hình R:R',
        meetsMin: false,
      };
    }
  }

  if (kind === 'UNKNOWN' || minRR === null || !Number.isFinite(minRR) || minRR <= 0) {
    return {
      status: 'UNKNOWN',
      actualNetRR: actual,
      requiredMinNetRR: null,
      diff: null,
      policyKind: kind,
      source: 'source' in policyView ? policyView.source : undefined,
      label: 'Chưa có ngưỡng xác nhận',
      meetsMin: false,
    };
  }

  if (kind === 'INVALID' || policyView.status === 'INVALID') {
    return {
      status: 'INVALID',
      actualNetRR: actual,
      requiredMinNetRR: null,
      diff: null,
      policyKind: kind,
      source: 'source' in policyView ? policyView.source : undefined,
      label: 'Cấu hình R:R không hợp lệ',
      meetsMin: false,
    };
  }

  // Strict full-precision decision comparison: NO rounding before meetsMin
  const meets = actual >= minRR;
  const rawDiff = actual - minRR;
  const diff = Number(rawDiff.toFixed(4)); // diagnostic diff only

  return {
    status: meets ? 'MET' : 'NOT_MET',
    actualNetRR: actual,
    requiredMinNetRR: minRR,
    diff,
    policyKind: kind,
    source: 'source' in policyView ? policyView.source : undefined,
    label: meets
      ? `Đạt yêu cầu (1:${actual.toFixed(2)} >= 1:${minRR.toFixed(2)})`
      : `Chưa đạt yêu cầu (1:${actual.toFixed(2)} < 1:${minRR.toFixed(2)})`,
    meetsMin: meets,
  };
}

/**
 * Re-evaluates an active chart DRAFT overlay under newly saved RR settings,
 * preserving exact geometry (entry, stopLoss, takeProfit, projectedBars, id, direction).
 */
export function rebaseDraftRrPolicy(
  draft: any,
  currentPolicy: CurrentPolicyContext,
  financialContext?: {
    accountEquity?: number;
    riskPct?: number;
    leverage?: number;
    marginMode?: 'ISOLATED' | 'CROSS';
  },
  calcFn?: any
): any {
  if (!draft || typeof draft !== 'object') return draft;
  if (draft.state !== 'draft') return draft;

  const nextMinNetRR = currentPolicy.value;
  if (nextMinNetRR == null || !Number.isFinite(nextMinNetRR) || nextMinNetRR < 1.2) {
    return draft;
  }

  const nextSource = currentPolicy.source || 'SYSTEM_CONFIG';
  const nextFingerprint = currentPolicy.fingerprint;

  const entry = draft.plannedEntry ?? draft.entry;
  const sl = draft.stopLoss;
  const tp = draft.takeProfit;
  const direction = draft.direction;

  if (entry == null || sl == null || tp == null) {
    return {
      ...draft,
      minNetRR: nextMinNetRR,
      rrPolicySource: nextSource,
      rrPolicyFingerprint: nextFingerprint,
    };
  }

  const compute = calcFn || calculateClientRiskReward;
  const equity = financialContext?.accountEquity ?? 1000.0;
  const rPct = financialContext?.riskPct ?? draft.riskPct ?? 0.25;
  const lev = financialContext?.leverage ?? draft.leverage ?? 5;
  const mm = (financialContext?.marginMode ?? draft.marginMode ?? 'ISOLATED') as 'ISOLATED' | 'CROSS';

  let calcResult: any = null;
  try {
    calcResult = compute(
      direction,
      entry,
      sl,
      tp,
      equity,
      rPct,
      nextMinNetRR,
      draft.quantity,
      lev,
      mm
    );
  } catch {
    // If calculator throws on invalid inputs, fallback cleanly
  }

  if (calcResult) {
    return {
      ...draft,
      minNetRR: nextMinNetRR,
      rrPolicySource: nextSource,
      rrPolicyFingerprint: nextFingerprint,
      quantity: calcResult.quantity,
      initialRiskUsdt: calcResult.netRiskUsdt,
      grossRewardUsdt: calcResult.grossRewardUsdt,
      grossLossUsdt: calcResult.grossLossUsdt,
      netRewardUsdt: calcResult.netRewardUsdt,
      multiplier: calcResult.multiplier,
      grossRR: calcResult.grossRR,
      estimatedNetRR: calcResult.estimatedNetRR,
      canExecute: calcResult.canExecute,
    };
  }

  return {
    ...draft,
    minNetRR: nextMinNetRR,
    rrPolicySource: nextSource,
    rrPolicyFingerprint: nextFingerprint,
  };
}

/**
 * Re-evaluates a SelectedTradeIntent if it represents a DRAFT.
 * Preserves entry/SL/TP coordinates while updating financial metrics and compliance.
 */
export function rebaseTradeIntentRrPolicy(
  intent: any,
  currentPolicy: CurrentPolicyContext,
  financialContext?: {
    accountEquity?: number;
    riskPct?: number;
    leverage?: number;
    marginMode?: 'ISOLATED' | 'CROSS';
  },
  calcFn?: any
): any {
  if (!intent || typeof intent !== 'object') return intent;
  if (intent.source !== 'DRAFT') return intent;

  const nextMinNetRR = currentPolicy.value;
  if (nextMinNetRR == null || !Number.isFinite(nextMinNetRR) || nextMinNetRR < 1.2) {
    return intent;
  }

  const nextSource = currentPolicy.source || 'SYSTEM_CONFIG';
  const nextFingerprint = currentPolicy.fingerprint;

  const entry = intent.plannedEntry;
  const sl = intent.stopLoss;
  const tp = intent.takeProfit;
  const direction = intent.direction;

  if (entry == null || sl == null || tp == null) {
    return {
      ...intent,
      minNetRR: nextMinNetRR,
      rrPolicySource: nextSource,
      rrPolicyFingerprint: nextFingerprint,
    };
  }

  const compute = calcFn || calculateClientRiskReward;
  const equity = financialContext?.accountEquity ?? intent.accountEquity ?? 1000.0;
  const rPct = financialContext?.riskPct ?? intent.riskPct ?? 0.25;
  const lev = financialContext?.leverage ?? intent.leverage ?? 5;
  const mm = (financialContext?.marginMode ?? intent.marginMode ?? 'ISOLATED') as 'ISOLATED' | 'CROSS';

  let calcResult: any = null;
  try {
    calcResult = compute(
      direction,
      entry,
      sl,
      tp,
      equity,
      rPct,
      nextMinNetRR,
      intent.quantity,
      lev,
      mm
    );
  } catch {}

  if (calcResult) {
    return {
      ...intent,
      minNetRR: nextMinNetRR,
      rrPolicySource: nextSource,
      rrPolicyFingerprint: nextFingerprint,
      quantity: calcResult.quantity,
      initialRiskUsdt: calcResult.netRiskUsdt,
      grossRewardUsdt: calcResult.grossRewardUsdt,
      grossLossUsdt: calcResult.grossLossUsdt,
      netRewardUsdt: calcResult.netRewardUsdt,
      multiplier: calcResult.multiplier,
      grossRR: calcResult.grossRR,
      estimatedNetRR: calcResult.estimatedNetRR,
    };
  }

  return {
    ...intent,
    minNetRR: nextMinNetRR,
    rrPolicySource: nextSource,
    rrPolicyFingerprint: nextFingerprint,
  };
}
