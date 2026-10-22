// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/v16_39_21_rr_observability.test.tsx
 * Aurum V16.39.21 — R:R Acceptance Observability & Invariance Test
 *
 * Verifies DOM attributes and state reflection:
 * 1. QuickDecisionSidebar:
 *    - data-testid="sidebar-selected-setup", data-selected-id, data-selected-direction
 *    - data-testid="sidebar-entry-price", sidebar-sl-price, sidebar-tp-price
 *    - data-testid="sidebar-open-trade-details"
 *    - data-testid="sidebar-net-rr-row":
 *      * data-rr-status="MET", data-rr-actual, data-rr-required at min 1.50
 *      * data-rr-status="NOT_MET", data-rr-actual, data-rr-required at min 2.50
 *      * Geometry invariance (prices & actual unchanged across minimum threshold changes)
 * 2. TradeDetailsDrawer:
 *    - data-testid="rr-policy-threshold-value":
 *      * "1:1.50 (Hiện hành)" under CURRENT READY 1.50
 *      * "1:2.50 (Hiện hành)" under CURRENT READY 2.50
 *      * "1:2.00 (Lúc ARM)" under frozen order snapshot
 */

import { act } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';

import { QuickDecisionSidebar } from '../components/QuickDecisionSidebar';
import { TradeDetailsDrawer } from '../components/TradeDetailsDrawer';
import type { SelectedTradeIntent } from '../App';
import type { CurrentPolicyContext } from '../utils/rrPolicyView';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    getCG01Evaluation: vi.fn().mockResolvedValue({
      symbol: 'XAUUSDT',
      state: 'STABLE',
      diagnostics: {},
    }),
  };
});

describe('Aurum V16.39.21 — R:R Observability & Invariance Suite', () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (root && container) {
      act(() => {
        root?.unmount();
      });
      container.remove();
    }
    container = null;
    root = null;
    vi.restoreAllMocks();
  });

  const canonicalLongSetupIntent: SelectedTradeIntent = {
    source: 'WATCH_SETUP',
    setup_id: 'e2e-mid-long',
    id: 'e2e-mid-long',
    symbol: 'XAUUSDT',
    timeframe: '15M',
    direction: 'LONG',
    plannedEntry: 2650.0,
    stopLoss: 2640.0,
    takeProfit: 2674.0,
    orderType: 'LIMIT',
    quantity: 0.2,
    initialRiskUsdt: 2.0,
    grossRR: 2.4,
    estimatedNetRR: 1.77,
    leverage: 5,
    marginMode: 'ISOLATED',
    status: 'READY',
    snapshotAt: Date.now(),
  };

  const canonicalShortSetupIntent: SelectedTradeIntent = {
    source: 'WATCH_SETUP',
    setup_id: 'e2e-mid-short',
    id: 'e2e-mid-short',
    symbol: 'XAUUSDT',
    timeframe: '15M',
    direction: 'SHORT',
    plannedEntry: 2650.0,
    stopLoss: 2660.0,
    takeProfit: 2626.0,
    orderType: 'LIMIT',
    quantity: 0.2,
    initialRiskUsdt: 2.0,
    grossRR: 2.4,
    estimatedNetRR: 1.77,
    leverage: 5,
    marginMode: 'ISOLATED',
    status: 'READY',
    snapshotAt: Date.now(),
  };

  describe('1. QuickDecisionSidebar DOM Attributes & Policy Met/Not-Met Reflection', () => {
    it('renders canonical LONG setup with MET status at min 1.50 and retains exact geometry', () => {
      act(() => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={canonicalLongSetupIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onFollowLatestSignal={() => {}}
            onOpenDetailsDrawer={() => {}}
            appliedMinNetRR={1.5}
            rrPolicySource="SYSTEM_CONFIG"
            rrPolicyFingerprint="ready-1.5"
            rrPolicyStatus="READY"
            isRrPolicyConfirmed={true}
          />
        );
      });

      const selectedSetupEl = container?.querySelector('[data-testid="sidebar-selected-setup"]');
      expect(selectedSetupEl).not.toBeNull();
      expect(selectedSetupEl?.getAttribute('data-selected-id')).toBe('e2e-mid-long');
      expect(selectedSetupEl?.getAttribute('data-selected-direction')).toBe('LONG');

      const entryEl = container?.querySelector('[data-testid="sidebar-entry-price"]');
      const slEl = container?.querySelector('[data-testid="sidebar-sl-price"]');
      const tpEl = container?.querySelector('[data-testid="sidebar-tp-price"]');
      expect(entryEl?.textContent).toContain('$2650');
      expect(slEl?.textContent).toContain('$2640');
      expect(tpEl?.textContent).toContain('$2674');

      const openDetailsBtn = container?.querySelector('[data-testid="sidebar-open-trade-details"]');
      expect(openDetailsBtn).not.toBeNull();

      const netRrRow = container?.querySelector('[data-testid="sidebar-net-rr-row"]');
      expect(netRrRow).not.toBeNull();
      expect(netRrRow?.getAttribute('data-rr-status')).toBe('MET');
      expect(Number(netRrRow?.getAttribute('data-rr-actual'))).toBeCloseTo(1.77, 2);
      expect(Number(netRrRow?.getAttribute('data-rr-required'))).toBe(1.5);
    });

    it('renders canonical LONG setup with NOT_MET status at min 2.50 with identical geometry', () => {
      act(() => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={canonicalLongSetupIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onFollowLatestSignal={() => {}}
            onOpenDetailsDrawer={() => {}}
            appliedMinNetRR={2.5}
            rrPolicySource="SYSTEM_CONFIG"
            rrPolicyFingerprint="ready-2.5"
            rrPolicyStatus="READY"
            isRrPolicyConfirmed={true}
          />
        );
      });

      const netRrRow = container?.querySelector('[data-testid="sidebar-net-rr-row"]');
      expect(netRrRow).not.toBeNull();
      expect(netRrRow?.getAttribute('data-rr-status')).toBe('NOT_MET');
      expect(Number(netRrRow?.getAttribute('data-rr-actual'))).toBeCloseTo(1.77, 2);
      expect(Number(netRrRow?.getAttribute('data-rr-required'))).toBe(2.5);

      // Geometry must be completely invariant
      const entryEl = container?.querySelector('[data-testid="sidebar-entry-price"]');
      const slEl = container?.querySelector('[data-testid="sidebar-sl-price"]');
      const tpEl = container?.querySelector('[data-testid="sidebar-tp-price"]');
      expect(entryEl?.textContent).toContain('$2650');
      expect(slEl?.textContent).toContain('$2640');
      expect(tpEl?.textContent).toContain('$2674');

      // Submit CTA should be disabled with can-submit false
      const ctaBtn = container?.querySelector('#entry-submit-cta');
      expect(ctaBtn?.getAttribute('data-can-submit')).toBe('false');
    });

    it('renders canonical SHORT setup with MET at 1.50 and NOT_MET at 2.50', () => {
      // 1. Check at 1.50
      act(() => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={canonicalShortSetupIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onFollowLatestSignal={() => {}}
            onOpenDetailsDrawer={() => {}}
            appliedMinNetRR={1.5}
            rrPolicySource="SYSTEM_CONFIG"
            rrPolicyFingerprint="ready-1.5"
            rrPolicyStatus="READY"
            isRrPolicyConfirmed={true}
          />
        );
      });

      let selectedSetupEl = container?.querySelector('[data-testid="sidebar-selected-setup"]');
      expect(selectedSetupEl?.getAttribute('data-selected-id')).toBe('e2e-mid-short');
      expect(selectedSetupEl?.getAttribute('data-selected-direction')).toBe('SHORT');

      let netRrRow = container?.querySelector('[data-testid="sidebar-net-rr-row"]');
      expect(netRrRow?.getAttribute('data-rr-status')).toBe('MET');
      expect(Number(netRrRow?.getAttribute('data-rr-required'))).toBe(1.5);

      // 2. Re-render at 2.50
      act(() => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={canonicalShortSetupIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onFollowLatestSignal={() => {}}
            onOpenDetailsDrawer={() => {}}
            appliedMinNetRR={2.5}
            rrPolicySource="SYSTEM_CONFIG"
            rrPolicyFingerprint="ready-2.5"
            rrPolicyStatus="READY"
            isRrPolicyConfirmed={true}
          />
        );
      });

      netRrRow = container?.querySelector('[data-testid="sidebar-net-rr-row"]');
      expect(netRrRow?.getAttribute('data-rr-status')).toBe('NOT_MET');
      expect(Number(netRrRow?.getAttribute('data-rr-required'))).toBe(2.5);

      const entryEl = container?.querySelector('[data-testid="sidebar-entry-price"]');
      const slEl = container?.querySelector('[data-testid="sidebar-sl-price"]');
      const tpEl = container?.querySelector('[data-testid="sidebar-tp-price"]');
      expect(entryEl?.textContent).toContain('$2650');
      expect(slEl?.textContent).toContain('$2660');
      expect(tpEl?.textContent).toContain('$2626');
    });
  });

  describe('2. TradeDetailsDrawer rr-policy-threshold-value Attribute & Labels', () => {
    it('displays "1:1.50 (Hiện hành)" under CURRENT READY 1.50', () => {
      const readyContext: CurrentPolicyContext = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        status: 'READY',
        isConfirmed: true,
        policyReadStatus: 'READY',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={canonicalLongSetupIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={1.5}
            currentPolicyContext={readyContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const thresholdEl = container?.querySelector('[data-testid="rr-policy-threshold-value"]');
      expect(thresholdEl).not.toBeNull();
      expect(thresholdEl?.textContent).toContain('1:1.50 (Hiện hành)');
    });

    it('displays "1:2.50 (Hiện hành)" under CURRENT READY 2.50', () => {
      const readyContext: CurrentPolicyContext = {
        value: 2.5,
        source: 'SYSTEM_CONFIG',
        status: 'READY',
        isConfirmed: true,
        policyReadStatus: 'READY',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={canonicalLongSetupIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={2.5}
            currentPolicyContext={readyContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const thresholdEl = container?.querySelector('[data-testid="rr-policy-threshold-value"]');
      expect(thresholdEl).not.toBeNull();
      expect(thresholdEl?.textContent).toContain('1:2.50 (Hiện hành)');
    });

    it('displays "1:2.00 (Lúc ARM)" for frozen order independent of CURRENT 2.50', () => {
      const frozenOrderIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        order_id: 'ord-frozen-v20',
        id: 'ord-frozen-v20',
        minNetRR: 2.0,
        rrPolicyStatus: 'KNOWN',
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2674.0,
        direction: 'LONG',
        quantity: 0.2,
        initialRiskUsdt: 2.0,
        grossRR: 2.4,
        estimatedNetRR: 1.77,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };

      const currentContext: CurrentPolicyContext = {
        value: 2.5,
        source: 'SYSTEM_CONFIG',
        status: 'READY',
        isConfirmed: true,
        policyReadStatus: 'READY',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={frozenOrderIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={2.5}
            currentPolicyContext={currentContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const thresholdEl = container?.querySelector('[data-testid="rr-policy-threshold-value"]');
      expect(thresholdEl).not.toBeNull();
      expect(thresholdEl?.textContent).toContain('1:2.00 (Lúc ARM)');
      expect(thresholdEl?.textContent).not.toContain('2.50');
    });
  });
});
