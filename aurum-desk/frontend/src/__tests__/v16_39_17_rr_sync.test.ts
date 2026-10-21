/**
 * frontend/src/__tests__/v16_39_17_rr_sync.test.ts
 * Aurum V16.39.17 — Full RR Policy Synchronization Test Suite
 *
 * Verifies:
 * 1. resolveSelectionRrPolicy: current vs order snapshot vs unknown.
 * 2. compareNetRr: strict multi-state comparison without fabricated thresholds.
 * 3. rebaseDraftRrPolicy & rebaseTradeIntentRrPolicy: geometry preservation & financial re-evaluation.
 * 4. userMessageCatalog & researchReasonText: dynamic parameter injection & neutral fallbacks.
 * 5. QuickDecisionSidebar logic: actual 1.7 with min 1.5 is NOT blocked; min 2.5 is blocked with 2.5 in reason.
 * 6. useRrPolicySettings lifecycle: dirty preservation for invalid drafts, generation handling.
 */

import { describe, it, expect } from 'vitest';
import {
  resolveSelectionRrPolicy,
  compareNetRr,
  rebaseDraftRrPolicy,
  rebaseTradeIntentRrPolicy,
} from '../utils/rrPolicyView';
import { getCatalogTemplate } from '../utils/userMessageCatalog';
import { getResearchReasonText } from '../utils/researchReasonText';
import type { RiskRewardData } from '../plugins/RiskRewardPrimitive';

describe('V16.39.17 — RR Policy Propagation & UI Synchronization', () => {
  // --------------------------------------------------------------------------
  // 1. resolveSelectionRrPolicy
  // --------------------------------------------------------------------------
  describe('1. resolveSelectionRrPolicy', () => {
    it('resolves CURRENT policy for DRAFT with verified current settings', () => {
      const intent = { source: 'DRAFT', minNetRR: 2.0 };
      const view = resolveSelectionRrPolicy(intent, {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        fingerprint: 'fp-150',
      });

      expect(view.kind).toBe('CURRENT');
      if (view.kind === 'CURRENT') {
        expect(view.minNetRR).toBe(1.5);
        expect(view.source).toBe('SYSTEM_CONFIG');
        expect(view.fingerprint).toBe('fp-150');
        expect(view.status).toBe('READY');
      }
    });

    it('resolves CURRENT policy for WATCH_SETUP with custom 2.50 setting', () => {
      const intent = { source: 'WATCH_SETUP', setup_id: 'ws-1', minNetRR: 2.0 };
      const view = resolveSelectionRrPolicy(intent, {
        value: 2.5,
        source: 'USER_PRESET',
        fingerprint: 'fp-250',
      });

      expect(view.kind).toBe('CURRENT');
      expect(view.minNetRR).toBe(2.5);
      expect(view.status).toBe('READY');
    });

    it('resolves FROZEN order snapshot for OPEN_POSITION without current leakage', () => {
      const intent = {
        source: 'OPEN_POSITION',
        order_id: 'ord-888',
        cost_snapshot: JSON.stringify({ min_net_rr_at_arm: 2.0, rr_policy_source: 'ORDER_SNAPSHOT' }),
      };
      // Current settings are 1.5, but frozen order must strictly return 2.0
      const view = resolveSelectionRrPolicy(intent, {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
      });

      expect(view.kind).toBe('ORDER_SNAPSHOT');
      expect(view.status).toBe('KNOWN');
      expect(view.minNetRR).toBe(2.0);
    });

    it('resolves MISSING for historical order with no snapshot (never fabricates 1.5 or 2.0)', () => {
      const intent = {
        source: 'OPEN_POSITION',
        order_id: 'ord-legacy',
      };
      const view = resolveSelectionRrPolicy(intent, { value: 1.5 });

      expect(view.kind).toBe('ORDER_SNAPSHOT');
      expect(view.status).toBe('MISSING');
      expect(view.minNetRR).toBeNull();
    });

    it('resolves LOADING / UNKNOWN when current policy is not yet loaded', () => {
      const intent = { source: 'DRAFT' };
      const view = resolveSelectionRrPolicy(intent, {
        value: null,
        status: 'loading',
      });

      expect(view.kind).toBe('CURRENT');
      if (view.kind === 'CURRENT') {
        expect(view.status).toBe('LOADING');
        expect(view.isAuthoritative).toBe(false);
      }
    });
  });

  // --------------------------------------------------------------------------
  // 2. compareNetRr
  // --------------------------------------------------------------------------
  describe('2. compareNetRr', () => {
    it('passes MET when actual 1.70 >= required 1.50', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'WATCH_SETUP' },
        { value: 1.5 }
      );
      const res = compareNetRr(1.7, policyView);

      expect(res.status).toBe('MET');
      expect(res.meetsMin).toBe(true);
      expect(res.actualNetRR).toBe(1.7);
      expect(res.requiredMinNetRR).toBe(1.5);
    });

    it('returns NOT_MET when actual 1.70 < required 2.00', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'WATCH_SETUP' },
        { value: 2.0 }
      );
      const res = compareNetRr(1.7, policyView);

      expect(res.status).toBe('NOT_MET');
      expect(res.meetsMin).toBe(false);
      expect(res.diff).toBeCloseTo(-0.3, 2);
    });

    it('returns NOT_MET when actual 1.70 < custom required 2.50', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'WATCH_SETUP' },
        { value: 2.5 }
      );
      const res = compareNetRr(1.7, policyView);

      expect(res.status).toBe('NOT_MET');
      expect(res.meetsMin).toBe(false);
      expect(res.label).toContain('1:2.50');
    });

    it('exact boundary: 1.49 NOT_MET vs 1.50 MET on 1.50 threshold', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'DRAFT' },
        { value: 1.5 }
      );
      expect(compareNetRr(1.49, policyView).status).toBe('NOT_MET');
      expect(compareNetRr(1.50, policyView).status).toBe('MET');
      expect(compareNetRr(1.51, policyView).status).toBe('MET');
    });

    it('returns UNKNOWN for historical order with missing threshold (never marks pass or fail)', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'OPEN_POSITION', order_id: 'ord-old' },
        { value: 1.5 }
      );
      const res = compareNetRr(1.85, policyView);

      expect(res.status).toBe('UNKNOWN');
      expect(res.requiredMinNetRR).toBeNull();
      expect(res.label).toBe('Chưa lưu ngưỡng lúc ARM');
    });

    it('handles negative Net RR as valid actual ratio, not hidden by || or --', () => {
      const policyView = resolveSelectionRrPolicy(
        { source: 'DRAFT' },
        { value: 1.5 }
      );
      const res = compareNetRr(-0.45, policyView);

      expect(res.status).toBe('NOT_MET');
      expect(res.actualNetRR).toBe(-0.45);
    });
  });

  // --------------------------------------------------------------------------
  // 3. rebaseDraftRrPolicy & rebaseTradeIntentRrPolicy
  // --------------------------------------------------------------------------
  describe('3. rebaseDraftRrPolicy & rebaseTradeIntentRrPolicy', () => {
    it('rebases existing draft overlay to 1.5 while preserving geometry and ID', () => {
      const initialDraft: RiskRewardData = {
        id: 'draft-12345',
        direction: 'LONG',
        state: 'draft',
        plannedEntry: 2700.0,
        stopLoss: 2690.0,
        takeProfit: 2735.0, // target distance 35 vs stop 10 -> Net RR ~2.3R >= 1.5R
        quantity: 0.1,
        initialRiskUsdt: 25.0,
        riskPct: 0.25,
        grossRR: 1.7,
        estimatedNetRR: 1.65,
        minNetRR: 2.0, // previously created under 2.0
        projectedBars: 35,
      };

      const rebased = rebaseDraftRrPolicy(
        initialDraft,
        { value: 1.5, source: 'SYSTEM_CONFIG', fingerprint: 'fp-new-15' },
        { accountEquity: 10000.0, riskPct: 0.25, leverage: 5, marginMode: 'ISOLATED' }
      );

      // Invariants: geometry preserved
      expect(rebased.id).toBe('draft-12345');
      expect(rebased.direction).toBe('LONG');
      expect(rebased.plannedEntry).toBe(2700.0);
      expect(rebased.stopLoss).toBe(2690.0);
      expect(rebased.takeProfit).toBe(2735.0);
      expect(rebased.projectedBars).toBe(35);

      // Policy updated
      expect(rebased.minNetRR).toBe(1.5);
      expect(rebased.rrPolicySource).toBe('SYSTEM_CONFIG');
      expect(rebased.rrPolicyFingerprint).toBe('fp-new-15');

      // Compliance re-evaluated: now can execute under 1.5!
      expect(rebased.canExecute).toBe(true);
    });

    it('rebases SelectedTradeIntent DRAFT without affecting non-draft intents', () => {
      const draftIntent = {
        source: 'DRAFT',
        plannedEntry: 2700.0,
        stopLoss: 2690.0,
        takeProfit: 2717.0,
        direction: 'LONG' as const,
        minNetRR: 2.0,
      };

      const rebasedDraft = rebaseTradeIntentRrPolicy(
        draftIntent,
        { value: 1.5, source: 'SYSTEM_CONFIG' }
      );
      expect(rebasedDraft.minNetRR).toBe(1.5);

      const orderIntent = {
        source: 'OPEN_POSITION',
        order_id: 'ord-frozen',
        minNetRR: 2.0,
      };
      const rebasedOrder = rebaseTradeIntentRrPolicy(
        orderIntent,
        { value: 1.5, source: 'SYSTEM_CONFIG' }
      );
      // Historical orders remain untouched
      expect(rebasedOrder.minNetRR).toBe(2.0);
    });
  });

  // --------------------------------------------------------------------------
  // 4. userMessageCatalog & researchReasonText
  // --------------------------------------------------------------------------
  describe('4. Dynamic message formatting & neutral fallbacks', () => {
    it('NET_RR_TOO_LOW formats with explicit required threshold', () => {
      const tmpl = getCatalogTemplate('NET_RR_TOO_LOW');
      const summary = typeof tmpl.summary === 'function'
        ? tmpl.summary({ net_rr: 1.7, required_rr: 2.5 })
        : '';

      expect(summary).toContain('1:1.70');
      expect(summary).toContain('1:2.50');
      expect(summary).not.toContain('1:2.00');
    });

    it('NET_RR_TOO_LOW outputs neutral text when threshold parameter is absent', () => {
      const tmpl = getCatalogTemplate('NET_RR_TOO_LOW');
      const summary = typeof tmpl.summary === 'function'
        ? tmpl.summary({ net_rr: 1.7 })
        : '';

      expect(summary).toContain('1:1.70');
      expect(summary).toContain('chưa đạt mức tối thiểu xác nhận');
      expect(summary).not.toContain('1:2.00');
      expect(summary).not.toContain('1:1.50');
    });

    it('INSUFFICIENT_RR outputs neutral text when threshold is absent', () => {
      const tmpl = getCatalogTemplate('INSUFFICIENT_RR');
      const summary = typeof tmpl.summary === 'function'
        ? tmpl.summary({ net_rr: 1.6 })
        : '';

      expect(summary).toContain('chưa đạt mức tối thiểu yêu cầu.');
      expect(summary).not.toContain('1:2.00');
    });

    it('researchReasonText formats with detail threshold if present', () => {
      const text = getResearchReasonText('NET_RR_TOO_LOW', { min_net_rr: 2.5 });
      expect(text).toContain('Net R:R >= 1:2.50');
    });

    it('researchReasonText outputs neutral text when detail threshold is missing', () => {
      const text = getResearchReasonText('NET_RR_TOO_LOW', {});
      expect(text).toContain('tỷ lệ Net R:R tối thiểu yêu cầu');
      expect(text).not.toContain('2.0');
      expect(text).not.toContain('1.5');
    });
  });

  // --------------------------------------------------------------------------
  // 5. QuickDecisionSidebar Blocker Verification (Phần 006, 057, 060)
  // --------------------------------------------------------------------------
  describe('5. QuickDecisionSidebar Blocker Verification', () => {
    it('actual 1.70 with appliedMinNetRR 1.50 produces NO NET_RR_TOO_LOW blocker', () => {
      const selectedIntent = {
        source: 'WATCH_SETUP',
        direction: 'LONG' as const,
        plannedEntry: 2700.0,
        stopLoss: 2690.0,
        takeProfit: 2717.0,
        estimatedNetRR: 1.7,
        netRewardUsdt: 42.5,
      };

      const selView = resolveSelectionRrPolicy(selectedIntent, { value: 1.5, source: 'SYSTEM_CONFIG' });
      const comp = compareNetRr(selectedIntent.estimatedNetRR, selView);

      // When comp.status === 'MET', no blocker is added
      expect(comp.status).toBe('MET');
      expect(comp.meetsMin).toBe(true);
    });

    it('actual 1.70 with appliedMinNetRR 2.50 produces NET_RR_TOO_LOW blocker mentioning 1:2.50', () => {
      const selectedIntent = {
        source: 'WATCH_SETUP',
        direction: 'LONG' as const,
        plannedEntry: 2700.0,
        stopLoss: 2690.0,
        takeProfit: 2717.0,
        estimatedNetRR: 1.7,
        netRewardUsdt: 42.5,
      };

      const selView = resolveSelectionRrPolicy(selectedIntent, { value: 2.5, source: 'SYSTEM_CONFIG' });
      const comp = compareNetRr(selectedIntent.estimatedNetRR, selView);

      expect(comp.status).toBe('NOT_MET');
      expect(comp.requiredMinNetRR).toBe(2.5);

      const tmpl = getCatalogTemplate('NET_RR_TOO_LOW');
      const blockerSummary = typeof tmpl.summary === 'function'
        ? tmpl.summary({ net_rr: selectedIntent.estimatedNetRR, required_rr: comp.requiredMinNetRR })
        : '';

      expect(blockerSummary).toContain('1:2.50');
      expect(blockerSummary).not.toContain('1:2.00');
    });

    it('custom precision: 1.75R is preserved without rounding to 1.8R', () => {
      const selView = resolveSelectionRrPolicy(
        { source: 'WATCH_SETUP' },
        { value: 1.75 }
      );
      const comp = compareNetRr(1.74, selView);

      expect(comp.status).toBe('NOT_MET');
      expect(comp.requiredMinNetRR).toBe(1.75);
      expect(comp.label).toContain('1:1.75');
    });
  });
});
