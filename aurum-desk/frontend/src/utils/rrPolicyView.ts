/**
 * frontend/src/utils/rrPolicyView.ts
 * Aurum V16.39.16 — Unified RR Policy View & Order Snapshot Adapter
 *
 * Responsibilities:
 * - Normalize server responses from /api/scalp/config and /api/v1/policy/trading.
 * - Resolve immutable order RR threshold from PaperOrder / Trade DTOs.
 * - Strict tri-state classification: KNOWN | MISSING | INVALID.
 * - Never fabricate 2.0 or current policy for missing historical order thresholds.
 */

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
 * Only returns status: 'READY' when the value is a finite number >= 1.5.
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
 * Extracts and resolves the immutable historical RR threshold from an order or trade DTO.
 * Precedence:
 * 1. orderDto.financial (if resolved display plan is already bound)
 * 2. orderDto.cost_snapshot (JSON or object) -> min_net_rr_at_arm, then min_net_rr
 * 3. orderDto.arm_decision_snapshot (JSON or object) -> min_net_rr_at_arm, then min_net_rr
 * 4. orderDto top-level min_net_rr_at_arm, then min_net_rr
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
 * Returns undefined for missing/invalid thresholds so that downstream
 * primitive renderers and inspectors treat it as unknown/historical.
 */
export function getOrderRrFields(orderDto: any): {
  minNetRR?: number;
  rrPolicySource?: string;
  rrPolicyFingerprint?: string;
} {
  const view = resolveOrderRrView(orderDto);
  if (view.status === 'KNOWN') {
    return {
      minNetRR: view.minNetRR,
      rrPolicySource: view.source,
      rrPolicyFingerprint: view.fingerprint,
    };
  }
  return {
    minNetRR: undefined,
    rrPolicySource: view.source ?? undefined,
    rrPolicyFingerprint: undefined,
  };
}
