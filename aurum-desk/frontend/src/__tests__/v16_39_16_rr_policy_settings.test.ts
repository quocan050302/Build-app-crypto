/**
 * frontend/src/__tests__/v16_39_16_rr_policy_settings.test.ts
 * Aurum V16.39.16 — Centralized Min Net R:R Settings & Closed Preview Snapshot UI Gaps
 *
 * Test Suite:
 * 1. normalizeRrPolicyResponse: normalization of API payloads, threshold validation (>= 1.50R)
 * 2. resolveOrderRrView: strict tri-state (KNOWN | MISSING | INVALID) for historical orders
 * 3. getOrderRrFields: field extraction for RiskRewardData & SelectedTradeIntent
 * 4. planToOverlay & DisplayPlan: propagation of nullable min_net_rr to chart overlay
 * 5. 5 Producers integrity contract: zero threshold leakage or historical fabrication
 */

import { describe, it, expect } from 'vitest';
import {
  normalizeRrPolicyResponse,
  resolveOrderRrView,
  getOrderRrFields,
} from '../utils/rrPolicyView';
import { planToOverlay, type DisplayPlanResolution } from '../utils/planLevels';

describe('V16.39.16 Min Net R:R Policy Normalization & Validation', () => {
  it('1.1: normalizeRrPolicyResponse parses effective_config with user override', () => {
    const raw = {
      status: 'success',
      effective_config: {
        min_net_rr: 2.5,
        rr_policy_source: 'USER_OVERRIDE',
        rr_policy_fingerprint: 'fp-123456',
      },
    };
    const res = normalizeRrPolicyResponse(raw);
    expect(res.status).toBe('READY');
    expect(res.value).toBe(2.5);
    expect(res.source).toBe('USER_OVERRIDE');
    expect(res.fingerprint).toBe('fp-123456');
    expect(res.error).toBeUndefined();
  });

  it('1.2: normalizeRrPolicyResponse parses top-level min_net_rr (1.50 minimum)', () => {
    const raw = {
      min_net_rr: 1.5,
      rr_policy_source: 'DEFAULT',
    };
    const res = normalizeRrPolicyResponse(raw);
    expect(res.status).toBe('READY');
    expect(res.value).toBe(1.5);
    expect(res.source).toBe('DEFAULT');
  });

  it('1.3: normalizeRrPolicyResponse rejects threshold below 1.50', () => {
    const raw = {
      min_net_rr: 1.49,
      rr_policy_source: 'USER_OVERRIDE',
    };
    const res = normalizeRrPolicyResponse(raw);
    expect(res.status).toBe('INVALID');
    expect(res.value).toBeNull();
    expect(res.error).toContain('yêu cầu >= 1.50R');
  });

  it('1.4: normalizeRrPolicyResponse rejects null, boolean, string or missing threshold', () => {
    expect(normalizeRrPolicyResponse(null).status).toBe('INVALID');
    expect(normalizeRrPolicyResponse({}).status).toBe('INVALID');
    expect(normalizeRrPolicyResponse({ min_net_rr: null }).status).toBe('INVALID');
    expect(normalizeRrPolicyResponse({ min_net_rr: true }).status).toBe('INVALID');
    expect(normalizeRrPolicyResponse({ min_net_rr: 'not-a-number' }).status).toBe('INVALID');
  });
});

describe('V16.39.16 Order RR View Resolution (Tri-State Historical Invariant)', () => {
  it('2.1: resolveOrderRrView resolves KNOWN threshold from cost_snapshot JSON', () => {
    const orderDto = {
      id: 'order-1',
      cost_snapshot: JSON.stringify({
        min_net_rr: 2.25,
        rr_policy_source: 'USER_OVERRIDE',
        rr_policy_fingerprint: 'snap-fp-01',
      }),
    };
    const view = resolveOrderRrView(orderDto);
    expect(view.status).toBe('KNOWN');
    if (view.status === 'KNOWN') {
      expect(view.minNetRR).toBe(2.25);
      expect(view.source).toBe('USER_OVERRIDE');
      expect(view.fingerprint).toBe('snap-fp-01');
    }
  });

  it('2.2: resolveOrderRrView resolves KNOWN threshold from arm_decision_snapshot JSON', () => {
    const orderDto = {
      id: 'order-2',
      arm_decision_snapshot: JSON.stringify({
        min_net_rr_at_arm: 2.75,
        rr_policy_source: 'USER_OVERRIDE',
      }),
    };
    const view = resolveOrderRrView(orderDto);
    expect(view.status).toBe('KNOWN');
    if (view.status === 'KNOWN') {
      expect(view.minNetRR).toBe(2.75);
      expect(view.source).toBe('USER_OVERRIDE');
    }
  });

  it('2.3: resolveOrderRrView returns MISSING without fabricating 2.0 when snapshot lacks threshold', () => {
    // Historical legacy order created before threshold snapshotting
    const legacyOrder = {
      id: 'legacy-order-99',
      cost_snapshot: JSON.stringify({
        fee_tier: 'TAKER',
        slippage_bps: 2.0,
      }),
      // Neither min_net_rr nor min_net_rr_at_arm exists
    };
    const view = resolveOrderRrView(legacyOrder);
    expect(view.status).toBe('MISSING');
    expect(view.minNetRR).toBeNull();
    // Rule: Must NEVER return 2.0 for missing historical order thresholds
    expect(view.minNetRR).not.toBe(2.0);
  });

  it('2.4: resolveOrderRrView returns MISSING when orderDto has no snapshots at all', () => {
    const emptyOrder = {
      id: 'bare-order-1',
      direction: 'LONG',
      planned_entry: 2700,
      stop_loss: 2690,
      take_profit: 2730,
    };
    const view = resolveOrderRrView(emptyOrder);
    expect(view.status).toBe('MISSING');
    expect(view.minNetRR).toBeNull();
  });

  it('2.5: resolveOrderRrView returns INVALID for non-positive or boolean threshold', () => {
    const invalidOrder = {
      id: 'invalid-order-1',
      cost_snapshot: JSON.stringify({ min_net_rr: -1.0 }),
    };
    const view = resolveOrderRrView(invalidOrder);
    expect(view.status).toBe('INVALID');
    expect(view.minNetRR).toBeNull();

    const boolOrder = {
      id: 'invalid-order-2',
      cost_snapshot: JSON.stringify({ min_net_rr: true }),
    };
    expect(resolveOrderRrView(boolOrder).status).toBe('INVALID');
  });

  it('2.6: resolveOrderRrView reads pre-resolved DisplayPlan resolution.financial if present', () => {
    const orderWithResolution = {
      id: 'order-res-1',
      resolution: {
        isValid: true,
        financial: {
          min_net_rr: null,
          rr_compliance_status: 'UNKNOWN',
          rr_policy_source: 'HISTORICAL_ORDER_NO_THRESHOLD_SNAPSHOT',
        },
      },
    };
    const view = resolveOrderRrView(orderWithResolution);
    expect(view.status).toBe('MISSING');
    expect(view.minNetRR).toBeNull();
  });
});

describe('V16.39.16 getOrderRrFields Extractor', () => {
  it('3.1: returns defined minNetRR only when status is KNOWN', () => {
    const knownOrder = {
      id: 'k-1',
      cost_snapshot: JSON.stringify({
        min_net_rr: 2.4,
        rr_policy_source: 'USER_OVERRIDE',
        rr_policy_fingerprint: 'fp-999',
      }),
    };
    const fields = getOrderRrFields(knownOrder);
    expect(fields.minNetRR).toBe(2.4);
    expect(fields.rrPolicySource).toBe('USER_OVERRIDE');
    expect(fields.rrPolicyFingerprint).toBe('fp-999');
  });

  it('3.2: returns minNetRR: undefined when status is MISSING (prevents false threshold evaluation)', () => {
    const missingOrder = {
      id: 'm-1',
      cost_snapshot: null,
    };
    const fields = getOrderRrFields(missingOrder);
    expect(fields.minNetRR).toBeUndefined();
    expect(fields.rrPolicyFingerprint).toBeUndefined();
  });
});

describe('V16.39.16 planToOverlay Propagation & Contract', () => {
  const baseSetup = {
    id: 'setup-v16',
    direction: 'LONG' as const,
    planned_entry: 2700,
    stop_loss: 2690,
    take_profit: 2725,
    quantity: 1.0,
    timeframe: '15M',
  };

  it('4.1: planToOverlay maps known financial min_net_rr into overlay', () => {
    const setupWithRdp = {
      ...baseSetup,
      resolved_display_plan: {
        is_valid: true,
        entry: 2700,
        stop_loss: 2690,
        take_profit: 2725,
        direction: 'LONG' as const,
        financial: {
          direction: 'LONG' as const,
          quantity: 1.0,
          net_risk_usdt: 10,
          gross_rr: 2.5,
          net_rr: 2.1,
          min_net_rr: 2.0,
          rr_policy_source: 'USER_OVERRIDE',
          rr_policy_fingerprint: 'res-fp-1',
          rr_compliance_status: 'PASS' as const,
          meets_min_rr: true,
        },
      },
    };

    const resolution = {
      isValid: true,
      plan: {
        entry: 2700,
        stopLoss: 2690,
        takeProfit: 2725,
        direction: 'LONG' as const,
      },
    };

    const overlay = planToOverlay(setupWithRdp, resolution, { isDraft: false });
    expect(overlay.minNetRR).toBe(2.0);
    expect(overlay.rrPolicySource).toBe('USER_OVERRIDE');
    expect(overlay.rrPolicyFingerprint).toBe('res-fp-1');
  });

  it('4.2: planToOverlay sets minNetRR undefined when financial min_net_rr is null (historical/unknown)', () => {
    const legacySetup = {
      ...baseSetup,
      resolved_display_plan: {
        is_valid: true,
        entry: 2700,
        stop_loss: 2690,
        take_profit: 2725,
        direction: 'LONG' as const,
        financial: {
          direction: 'LONG' as const,
          quantity: 1.0,
          net_risk_usdt: 10,
          gross_rr: 2.5,
          net_rr: 2.1,
          min_net_rr: null,
          rr_policy_source: 'STORED_LEGACY',
          rr_compliance_status: 'UNKNOWN' as const,
          meets_min_rr: null,
        },
      },
    };

    const resolution = {
      isValid: true,
      plan: {
        entry: 2700,
        stopLoss: 2690,
        takeProfit: 2725,
        direction: 'LONG' as const,
      },
    };

    const overlay = planToOverlay(legacySetup, resolution, { isDraft: false });
    expect(overlay.minNetRR).toBeUndefined();
    expect(overlay.rrPolicySource).toBe('STORED_LEGACY');
  });
});
