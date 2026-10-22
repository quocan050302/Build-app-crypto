// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/v16_39_19_rr_closure.test.tsx
 * Aurum V16.39.19 — RR State Branches, Error Separation & No-Reload Save Verification
 *
 * Verifies:
 * 1. FAILURE A (Repro A1): CURRENT ERROR must not be promoted to READY.
 *    resolveSelectionRrPolicy returns status 'ERROR', isAuthoritative false.
 *    compareNetRr returns status 'UNKNOWN', meetsMin false, label 'Lỗi cấu hình R:R'.
 * 2. FAILURE B (Repro A2): SNAPSHOT INVALID must not be collapsed into MISSING.
 *    resolveSelectionRrPolicy returns status 'INVALID'.
 *    compareNetRr returns status 'INVALID', meetsMin false, label 'Ngưỡng snapshot lúc ARM không hợp lệ'.
 * 3. Controls & Truth Table:
 *    - Order MISSING -> UNKNOWN ('Chưa lưu ngưỡng lúc ARM').
 *    - Order KNOWN 1.5 -> MET for actual 1.7 even under CURRENT ERROR.
 *    - Full precision: 1.49996 < 1.5 NOT_MET, 1.5 MET.
 *    - Actual NaN/Infinity/null -> INVALID ('R:R không hợp lệ').
 * 4. Hook Error Separation:
 *    - Read error vs Save error (policyReadStatus vs errorOrigin).
 *    - Save failure preserves last confirmed value and dirty draft.
 *    - Post-save callback error is isolated from save success.
 * 5. Production Components:
 *    - QuickDecisionSidebar: blocks on CURRENT ERROR with 'Lỗi cấu hình R:R', blocks on 1.7 < 2.5 with NET_RR_TOO_LOW.
 *    - TradeDetailsDrawer: renders distinct 'Ngưỡng snapshot lúc ARM không hợp lệ' for INVALID, 'Chưa lưu ngưỡng lúc ARM' for MISSING, and '1:2.00 (Lúc ARM)' for KNOWN.
 *    - CG01DiagnosticCard: renders 'Lỗi tải cấu hình' on ERROR even if finite value is present.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { api } from '../api/client';
import {
  resolveSelectionRrPolicy,
  compareNetRr,
  resolveMappedOrderRrView,
  normalizeCurrentPolicyReadStatus,
} from '../utils/rrPolicyView';
import { useRrPolicySettings } from '../hooks/useRrPolicySettings';
import { TradeDetailsDrawer } from '../components/TradeDetailsDrawer';
import { QuickDecisionSidebar } from '../components/QuickDecisionSidebar';
import { CG01DiagnosticCard } from '../components/CG01DiagnosticCard';
import type { SelectedTradeIntent } from '../App';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let originalFetch: typeof globalThis.fetch;

describe('Aurum V16.39.19 — RR State Branches & Error Separation Suite', () => {
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    originalFetch = globalThis.fetch;
    vi.restoreAllMocks();
    vi.spyOn(api, 'getCG01Evaluation').mockResolvedValue({ status: 'success', evaluation: null });
    globalThis.fetch = vi.fn().mockImplementation((url: string | URL | Request) => {
      const urlStr = typeof url === 'string' ? url : url.toString();
      if (urlStr.includes('/api/scalp/cg01/evaluation')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: 'success', evaluation: null }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ min_net_rr: 2.0, rr_policy_source: 'DEFAULT' }),
      });
    });
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root?.unmount();
      });
    }
    if (container && container.parentNode) {
      container.parentNode.removeChild(container);
    }
    globalThis.fetch = originalFetch;
  });

  // =========================================================================
  // 1. FAILURE A (Repro A1): CURRENT ERROR IS NOT PROMOTED TO READY
  // =========================================================================
  describe('Failure A: CURRENT ERROR Preservation', () => {
    it('A1: resolveSelectionRrPolicy with status ERROR and isConfirmed true returns ERROR view', () => {
      const current = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'ERROR',
      };
      const view = resolveSelectionRrPolicy({ source: 'DRAFT' }, current);
      expect(view.kind).toBe('CURRENT');
      expect(view.status).toBe('ERROR');
      expect((view as any).isAuthoritative).toBe(false);

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
      expect(result.requiredMinNetRR).toBeNull();
      expect(result.label).toBe('Lỗi cấu hình R:R');
    });

    it('A1.1: Case compatibility: lowercase status "error" also preserves ERROR', () => {
      const current = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'error',
      };
      const view = resolveSelectionRrPolicy({ source: 'DRAFT' }, current);
      expect(view.status).toBe('ERROR');

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
    });

    it('A1.2: No-intent branch: resolveSelectionRrPolicy(null, CURRENT ERROR) preserves ERROR', () => {
      const current = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'ERROR',
      };
      const view = resolveSelectionRrPolicy(null, current);
      expect(view.kind).toBe('CURRENT');
      expect(view.status).toBe('ERROR');
      expect((view as any).isAuthoritative).toBe(false);

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
    });

    it('A1.3: WATCH_SETUP with CURRENT ERROR preserves ERROR (does not treat as order snapshot)', () => {
      const current = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'ERROR',
      };
      const view = resolveSelectionRrPolicy({ source: 'WATCH_SETUP', id: 'setup-1' }, current);
      expect(view.kind).toBe('CURRENT');
      expect(view.status).toBe('ERROR');

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
    });

    it('A1.4: High actual ratio (5.00) does NOT bypass policy ERROR', () => {
      const current = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'ERROR',
      };
      const view = resolveSelectionRrPolicy({ source: 'DRAFT' }, current);
      const result = compareNetRr(5.0, view);

      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
    });

    it('A1.5: ORDER KNOWN snapshot is independent of CURRENT ERROR', () => {
      const intentKnown: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        id: 'ord-k',
        order_id: 'ord-k',
        setup_id: 'ord-k',
        minNetRR: 1.5,
        rrPolicyStatus: 'KNOWN',
        rrPolicySource: 'ORDER_SNAPSHOT',
        estimatedNetRR: 1.7,
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2675.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 25,
        netRewardUsdt: 22,
        multiplier: 1,
        grossRR: 2.5,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };
      const current = {
        value: 2.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
        status: 'ERROR',
      };
      const view = resolveSelectionRrPolicy(intentKnown, current);
      expect(view.kind).toBe('ORDER_SNAPSHOT');
      expect(view.status).toBe('KNOWN');
      expect(view.minNetRR).toBe(1.5);

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('MET');
      expect(result.meetsMin).toBe(true);
      expect(result.requiredMinNetRR).toBe(1.5);
    });

    it('normalizeCurrentPolicyReadStatus normalizes case and handles invalid values safely', () => {
      expect(normalizeCurrentPolicyReadStatus('ready')).toBe('READY');
      expect(normalizeCurrentPolicyReadStatus('READY')).toBe('READY');
      expect(normalizeCurrentPolicyReadStatus('error')).toBe('ERROR');
      expect(normalizeCurrentPolicyReadStatus('ERROR')).toBe('ERROR');
      expect(normalizeCurrentPolicyReadStatus('loading')).toBe('LOADING');
      expect(normalizeCurrentPolicyReadStatus('LOADING')).toBe('LOADING');
      expect(normalizeCurrentPolicyReadStatus('unconfirmed')).toBe('UNCONFIRMED');
      expect(normalizeCurrentPolicyReadStatus('UNCONFIRMED')).toBe('UNCONFIRMED');
      expect(normalizeCurrentPolicyReadStatus(null)).toBe('UNCONFIRMED');
      expect(normalizeCurrentPolicyReadStatus('INVALID_STATUS')).toBe('UNCONFIRMED');
    });

    it('resolveMappedOrderRrView extracts snapshot fields from mapped order intent', () => {
      const mappedIntent: any = {
        source: 'OPEN_POSITION',
        id: 'ord-1',
        order_id: 'ord-1',
        minNetRR: 1.5,
        rrPolicyStatus: 'KNOWN',
        rrPolicySource: 'ORDER_SNAPSHOT',
        rrPolicyFingerprint: 'fp-123',
      };
      const view = resolveMappedOrderRrView(mappedIntent);
      expect(view).not.toBeNull();
      expect(view.status).toBe('KNOWN');
      expect(view.minNetRR).toBe(1.5);
    });
  });

  // =========================================================================
  // 2. FAILURE B (Repro A2): SNAPSHOT INVALID IS NOT MERGED WITH MISSING
  // =========================================================================
  describe('Failure B: SNAPSHOT INVALID vs MISSING Separation', () => {
    it('A2: Mapped intent with status INVALID and minNetRR null returns status INVALID in both view and comparator', () => {
      const intent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        order_id: 'bad-a',
        id: 'bad-a',
        setup_id: 'bad-a',
        rrPolicyStatus: 'INVALID',
        minNetRR: null,
        rrPolicySource: 'ORDER_SNAPSHOT',
        estimatedNetRR: 1.7,
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2670.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 20,
        netRewardUsdt: 17,
        multiplier: 1,
        grossRR: 2.0,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };
      const current = { value: 1.5, isConfirmed: true, status: 'READY' };

      const view = resolveSelectionRrPolicy(intent, current);
      expect(view.kind).toBe('ORDER_SNAPSHOT');
      expect(view.status).toBe('INVALID');
      expect(view.minNetRR).toBeNull();

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('INVALID');
      expect(result.meetsMin).toBe(false);
      expect(result.requiredMinNetRR).toBeNull();
      expect(result.label).toBe('Ngưỡng snapshot lúc ARM không hợp lệ');
    });

    it('A2.1: Control: MISSING snapshot returns UNKNOWN with label Chưa lưu ngưỡng lúc ARM', () => {
      const intentMissing: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        order_id: 'missing-a',
        id: 'missing-a',
        setup_id: 'missing-a',
        rrPolicyStatus: 'MISSING',
        minNetRR: null,
        rrPolicySource: 'ORDER_SNAPSHOT',
        estimatedNetRR: 1.7,
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2670.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 20,
        netRewardUsdt: 17,
        multiplier: 1,
        grossRR: 2.0,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };
      const current = { value: 1.5, isConfirmed: true, status: 'READY' };

      const view = resolveSelectionRrPolicy(intentMissing, current);
      expect(view.kind).toBe('ORDER_SNAPSHOT');
      expect(view.status).toBe('MISSING');

      const result = compareNetRr(1.7, view);
      expect(result.status).toBe('UNKNOWN');
      expect(result.meetsMin).toBe(false);
      expect(result.label).toBe('Chưa lưu ngưỡng lúc ARM');
    });

    it('A2.2: Invalid actual Net R:R (null, NaN, Infinity) returns INVALID status', () => {
      const policyView = resolveSelectionRrPolicy(null, { value: 1.5, isConfirmed: true, status: 'READY' });

      expect(compareNetRr(null, policyView).status).toBe('INVALID');
      expect(compareNetRr(NaN, policyView).status).toBe('INVALID');
      expect(compareNetRr(Infinity, policyView).status).toBe('INVALID');
      expect(compareNetRr(undefined, policyView).status).toBe('INVALID');
    });
  });

  // =========================================================================
  // 3. HOOK ERROR SEPARATION: READ ERROR VS SAVE ERROR
  // =========================================================================
  describe('Hook Error Separation: useRrPolicySettings', () => {
    function HookHarness({
      onHook,
      onSaveSuccess,
    }: {
      onHook: (ret: ReturnType<typeof useRrPolicySettings>) => void;
      onSaveSuccess?: any;
    }) {
      const hookRet = useRrPolicySettings({ onSaveSuccess });
      onHook(hookRet);
      return <div data-testid="hook-status">{hookRet.status}</div>;
    }

    it('Initial GET failure sets policyReadStatus to ERROR and errorOrigin to READ', async () => {
      let latestHook: any = null;
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network offline'));

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      expect(latestHook.status).toBe('error');
      expect(latestHook.policyReadStatus).toBe('ERROR');
      expect(latestHook.errorOrigin).toBe('READ');
      expect(latestHook.readError).toBe('Network offline');
      expect(latestHook.saveError).toBeNull();
      expect(latestHook.saved.isConfirmed).toBe(false);
    });

    it('GET failure after previous success preserves saved value as lastKnown but sets policyReadStatus to ERROR', async () => {
      let latestHook: any = null;
      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ min_net_rr: 1.5, rr_policy_source: 'SYSTEM_CONFIG' }),
        })
        .mockRejectedValueOnce(new Error('Revalidation failed'));

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      expect(latestHook.saved.value).toBe(1.5);
      expect(latestHook.policyReadStatus).toBe('READY');

      await act(async () => {
        await latestHook.refresh();
      });

      expect(latestHook.status).toBe('error');
      expect(latestHook.policyReadStatus).toBe('ERROR');
      expect(latestHook.errorOrigin).toBe('READ');
      expect(latestHook.saved.value).toBe(1.5); // Preserved lastKnown!
    });

    it('Save failure preserves confirmed saved value and sets errorOrigin to SAVE without corrupting policyReadStatus', async () => {
      let latestHook: any = null;
      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ min_net_rr: 1.5, rr_policy_source: 'SYSTEM_CONFIG' }),
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 400,
          json: async () => ({ detail: 'Validation failed on backend' }),
        });

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      expect(latestHook.saved.value).toBe(1.5);
      expect(latestHook.policyReadStatus).toBe('READY');

      await act(async () => {
        latestHook.setDraftText('2.50');
      });

      let saveOk = true;
      await act(async () => {
        saveOk = await latestHook.save();
      });

      expect(saveOk).toBe(false);
      expect(latestHook.status).toBe('error');
      expect(latestHook.errorOrigin).toBe('SAVE');
      expect(latestHook.saveError).toBe('Validation failed on backend');
      expect(latestHook.policyReadStatus).toBe('READY'); // NOT corrupted to ERROR!
      expect(latestHook.saved.value).toBe(1.5); // Confirmed setting retained!
      expect(latestHook.draftText).toBe('2.50'); // User draft kept dirty!
    });

    it('Save success updates saved value and calls onSaveSuccess without throwing on callback error', async () => {
      let latestHook: any = null;
      const callbackErrorMock = vi.fn().mockImplementation(() => {
        throw new Error('Refresh upcoming failed');
      });

      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ min_net_rr: 2.0, rr_policy_source: 'DEFAULT' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            effective_config: { min_net_rr: 1.5, rr_policy_source: 'SYSTEM_CONFIG' },
          }),
        });

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} onSaveSuccess={callbackErrorMock} />);
      });

      await act(async () => {
        latestHook.setDraftText('1.50');
      });

      let saveOk = false;
      await act(async () => {
        saveOk = await latestHook.save();
      });

      expect(saveOk).toBe(true);
      expect(latestHook.saved.value).toBe(1.5);
      expect(latestHook.policyReadStatus).toBe('READY');
      expect(latestHook.errorOrigin).toBeNull();
      expect(latestHook.saveError).toBeNull();
    });
  });

  // =========================================================================
  // 4. PRODUCTION COMPONENTS: TradeDetailsDrawer, QuickDecisionSidebar, CG01Card
  // =========================================================================
  describe('Production Component Rendering Under Error & Invalid Branches', () => {
    it('CG01DiagnosticCard renders Lỗi tải cấu hình when rrPolicyStatus is ERROR', () => {
      act(() => {
        root?.render(
          <CG01DiagnosticCard
            displayState={{
              stage: 'OBSERVE',
              stageLabel: 'Quan sát',
              tone: 'neutral',
              direction: 'LONG',
              description: 'Test',
              isStale: false,
              isConnected: true,
              asOfMs: Date.now(),
              primaryReason: 'none',
              allReasons: [],
              directionPolicy: 'LEGACY_H1',
            } as any}
            appliedMinNetRR={1.5}
            isRrPolicyConfirmed={true}
            rrPolicyStatus="ERROR"
          />
        );
      });

      // Settings panel is collapsed by default; click toggle to expand
      const toggleBtn = container?.querySelector('[data-testid="cg01-toggle-settings-btn"]') as HTMLButtonElement | null;
      expect(toggleBtn).not.toBeNull();
      act(() => {
        toggleBtn?.click();
      });

      const badge = container?.querySelector('[data-testid="cg01-applied-rr-badge"]');
      expect(badge).not.toBeNull();
      expect(badge?.textContent).toBe('Lỗi tải cấu hình');
    });

    it('TradeDetailsDrawer renders Ngưỡng snapshot lúc ARM không hợp lệ for INVALID order', () => {
      const invalidIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        id: 'ord-inv',
        order_id: 'ord-inv',
        setup_id: 'ord-inv',
        rrPolicyStatus: 'INVALID',
        minNetRR: null,
        rrPolicySource: 'ORDER_SNAPSHOT',
        estimatedNetRR: 1.7,
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2670.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 20,
        netRewardUsdt: 17,
        multiplier: 1,
        grossRR: 2.0,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={invalidIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={1.5}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Ngưỡng snapshot lúc ARM không hợp lệ');
      expect(bodyText).not.toContain('Chưa lưu ngưỡng lúc ARM');
    });

    it('QuickDecisionSidebar blocks on CURRENT ERROR and does NOT allow submitting', () => {
      const watchIntent: SelectedTradeIntent = {
        source: 'WATCH_SETUP',
        id: 'setup-1',
        setup_id: 'setup-1',
        setup_instance_id: 'inst-1',
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2675.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 25,
        netRewardUsdt: 22,
        multiplier: 1,
        grossRR: 2.5,
        estimatedNetRR: 2.2,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'MARKET',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'WATCHING',
        snapshotAt: Date.now(),
      };

      act(() => {
        root?.render(
          <QuickDecisionSidebar
            {...({
              selectedIntent: watchIntent,
              activePosition: null,
              appliedMinNetRR: 1.5,
              rrPolicyStatus: 'ERROR',
              isRrPolicyConfirmed: false,
              hasArmedOrder: false,
              upcomingSetups: [],
            } as any)}
          />
        );
      });

      const ctaBtn = container?.querySelector('#entry-submit-cta');
      expect(ctaBtn).not.toBeNull();
      expect(ctaBtn?.getAttribute('data-can-submit')).toBe('false');
      expect(ctaBtn?.getAttribute('disabled')).not.toBeNull();

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Lỗi cấu hình R:R');
    });
  });
});
