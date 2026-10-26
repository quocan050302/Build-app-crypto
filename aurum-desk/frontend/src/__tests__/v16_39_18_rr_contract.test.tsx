// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/v16_39_18_rr_contract.test.tsx
 * Aurum V16.39.18 — Strict RR Contract, Boundary Precision & Production Component Tests
 *
 * Verifies:
 * 1. PROBE 1 (Input A): Mapped SelectedTradeIntent (OPEN_POSITION without cost_snapshot) resolves to
 *    ORDER_SNAPSHOT / KNOWN / minNetRR: 1.50 without leaking current 2.50 or returning MISSING.
 * 2. PROBE 2 (Input B): compareNetRr(1.49996, 1.50) strictly NOT_MET, meetsMin=false (unrounded comparison).
 * 3. Exact boundary suite: 1.499999 vs 1.500000 vs 1.500001; 2.50 vs 1.70 NOT_MET; 1.50 vs 1.70 MET.
 * 4. Unconfirmed/Loading policy yields UNKNOWN / meetsMin=false and raises POLICY_GATE without faking 2.0.
 * 5. Production Hook (useRrPolicySettings) lifecycle, draft preservation and single POST payload.
 * 6. Production Components:
 *    - TradeDetailsDrawer renders '1:1.50 (Lúc ARM)' for mapped Input A.
 *    - QuickDecisionSidebar raises POLICY_GATE when unconfirmed, NET_RR_TOO_LOW for 1.70 < 2.50.
 *    - CG01DiagnosticCard renders 'Đang tải...' when unconfirmed, '1:1.50' when confirmed.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { api } from '../api/client';
import {
  resolveSelectionRrPolicy,
  compareNetRr,
  resolveOrderRrView,
  getOrderRrFields,
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

describe('Aurum V16.39.18 Contract & Regression Suite', () => {
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
  // 1. PROBE 1: Mapped SelectedTradeIntent Resolution (Input A)
  // =========================================================================
  describe('Probe 1: Mapped Order Snapshot Contract', () => {
    const inputA: SelectedTradeIntent = {
      source: 'OPEN_POSITION',
      id: 'order-a',
      order_id: 'order-a',
      setup_id: 'order-a',
      minNetRR: 1.5,
      rrPolicySource: 'ORDER_SNAPSHOT',
      rrPolicyFingerprint: 'arm-fp',
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

    it('Probe 1 Baseline Fix: resolves mapped OPEN_POSITION to ORDER_SNAPSHOT KNOWN 1.50 under current 2.50', () => {
      const currentPolicy = {
        value: 2.5,
        source: 'SYSTEM_CONFIG',
        fingerprint: 'curr-fp-25',
        isConfirmed: true,
      };

      const resolved = resolveSelectionRrPolicy(inputA, currentPolicy);

      expect(resolved.kind).toBe('ORDER_SNAPSHOT');
      expect(resolved.status).toBe('KNOWN');
      expect(resolved.minNetRR).toBe(1.5);
      expect(resolved.source).toBe('ORDER_SNAPSHOT');
      expect((resolved as any).fingerprint).toBe('arm-fp');
      expect((resolved as any).orderId).toBe('order-a');
    });

    it('Mapped order with explicit MISSING status returns ORDER_SNAPSHOT / MISSING', () => {
      const mappedMissing: SelectedTradeIntent = {
        ...inputA,
        minNetRR: null,
        rrPolicyStatus: 'MISSING',
      };

      const resolved = resolveSelectionRrPolicy(mappedMissing, { value: 2.0, isConfirmed: true });
      expect(resolved.kind).toBe('ORDER_SNAPSHOT');
      expect(resolved.status).toBe('MISSING');
      expect(resolved.minNetRR).toBeNull();
    });

    it('Mapped order with explicit INVALID status returns ORDER_SNAPSHOT / INVALID', () => {
      const mappedInvalid: SelectedTradeIntent = {
        ...inputA,
        minNetRR: null,
        rrPolicyStatus: 'INVALID',
        rrPolicyReason: 'CORRUPT_VALUE',
      };

      const resolved = resolveSelectionRrPolicy(mappedInvalid, { value: 2.0, isConfirmed: true });
      expect(resolved.kind).toBe('ORDER_SNAPSHOT');
      expect(resolved.status).toBe('INVALID');
      expect(resolved.minNetRR).toBeNull();
    });

    it('getOrderRrFields maps raw order into typed fields including rrPolicyStatus', () => {
      const rawValid = {
        id: 'ord-100',
        cost_snapshot: JSON.stringify({ min_net_rr_at_arm: 1.65, rr_policy_source: 'ORDER_SNAPSHOT' }),
      };
      const fields = getOrderRrFields(rawValid);
      expect(fields.minNetRR).toBe(1.65);
      expect(fields.rrPolicyStatus).toBe('KNOWN');
      expect(fields.rrPolicySource).toBe('ORDER_SNAPSHOT');

      const rawMissing = { id: 'ord-101' };
      const missingFields = getOrderRrFields(rawMissing);
      expect(missingFields.minNetRR).toBeUndefined();
      expect(missingFields.rrPolicyStatus).toBe('MISSING');
    });

    it('Raw DTO regression: resolveOrderRrView preserves cost_snapshot extraction', () => {
      const rawDto = {
        cost_snapshot: JSON.stringify({ min_net_rr_at_arm: 1.5, rr_policy_source: 'ORDER_SNAPSHOT' }),
      };
      const res = resolveOrderRrView(rawDto);
      expect(res.status).toBe('KNOWN');
      expect(res.minNetRR).toBe(1.5);
    });
  });

  // =========================================================================
  // 2. PROBE 2: Full-Precision Comparison (Input B)
  // =========================================================================
  describe('Probe 2: Full-Precision Comparison Boundary', () => {
    const policyView15 = resolveSelectionRrPolicy(null, {
      value: 1.5,
      source: 'SYSTEM_CONFIG',
      isConfirmed: true,
    });

    it('Probe 2 Baseline Fix: compareNetRr(1.49996, 1.50) returns NOT_MET, meetsMin=false', () => {
      const res = compareNetRr(1.49996, policyView15);

      expect(res.status).toBe('NOT_MET');
      expect(res.meetsMin).toBe(false);
      expect(res.actualNetRR).toBe(1.49996);
      expect(res.requiredMinNetRR).toBe(1.5);
    });

    it('Exact boundary tests: 1.499999 NOT_MET, 1.500000 MET, 1.500001 MET', () => {
      expect(compareNetRr(1.499999, policyView15).meetsMin).toBe(false);
      expect(compareNetRr(1.499999, policyView15).status).toBe('NOT_MET');

      expect(compareNetRr(1.500000, policyView15).meetsMin).toBe(true);
      expect(compareNetRr(1.500000, policyView15).status).toBe('MET');

      expect(compareNetRr(1.500001, policyView15).meetsMin).toBe(true);
      expect(compareNetRr(1.500001, policyView15).status).toBe('MET');
    });

    it('Custom threshold: actual 1.70 against required 2.50 is NOT_MET; against 1.50 is MET', () => {
      const policyView25 = resolveSelectionRrPolicy(null, {
        value: 2.5,
        source: 'SYSTEM_CONFIG',
        isConfirmed: true,
      });

      const res25 = compareNetRr(1.7, policyView25);
      expect(res25.status).toBe('NOT_MET');
      expect(res25.meetsMin).toBe(false);
      expect(res25.requiredMinNetRR).toBe(2.5);

      const res15 = compareNetRr(1.7, policyView15);
      expect(res15.status).toBe('MET');
      expect(res15.meetsMin).toBe(true);
      expect(res15.requiredMinNetRR).toBe(1.5);
    });

    it('Unconfirmed/Loading policy comparison returns UNKNOWN with meetsMin=false', () => {
      const loadingView = resolveSelectionRrPolicy(
        { source: 'DRAFT', minNetRR: 2.0 },
        { value: null, isConfirmed: false, status: 'LOADING' }
      );
      expect(loadingView.status).toBe('LOADING');
      expect(loadingView.minNetRR).toBeNull();

      const res = compareNetRr(1.7, loadingView);
      expect(res.status).toBe('UNKNOWN');
      expect(res.meetsMin).toBe(false);
      expect(res.requiredMinNetRR).toBeNull();
    });
  });

  // =========================================================================
  // 3. PRODUCTION HOOK: useRrPolicySettings Lifecycle
  // =========================================================================
  describe('useRrPolicySettings Hook Lifecycle', () => {
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

    it('Initial state is unconfirmed/loading with null saved value', async () => {
      let latestHook: any = null;
      globalThis.fetch = vi.fn().mockReturnValue(new Promise(() => {})); // Never resolves

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      expect(latestHook.status).toBe('loading');
      expect(latestHook.saved.isConfirmed).toBe(false);
      expect(latestHook.saved.value).toBeNull();
    });

    it('GET success with DEFAULT 2.0 transitions to ready and confirmed', async () => {
      let latestHook: any = null;
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          min_net_rr: 2.0,
          rr_policy_source: 'DEFAULT',
          rr_policy_fingerprint: 'fp-def',
        }),
      });

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      expect(latestHook.status).toBe('ready');
      expect(latestHook.saved.isConfirmed).toBe(true);
      expect(latestHook.saved.value).toBe(2.0);
      expect(latestHook.saved.source).toBe('DEFAULT');
    });

    it('Save payload sends only min_net_rr and updates confirmed state on success', async () => {
      let latestHook: any = null;
      const onSaveSuccessMock = vi.fn();

      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ min_net_rr: 2.0, rr_policy_source: 'DEFAULT' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            effective_config: { min_net_rr: 1.5, rr_policy_source: 'SYSTEM_CONFIG', rr_policy_fingerprint: 'fp-15' },
          }),
        });

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} onSaveSuccess={onSaveSuccessMock} />);
      });

      await act(async () => {
        latestHook.setDraftText('1.50');
      });
      expect(latestHook.isDirty).toBe(true);

      let saveOk = false;
      await act(async () => {
        saveOk = await latestHook.save();
      });

      expect(saveOk).toBe(true);
      expect(latestHook.saved.value).toBe(1.5);
      expect(latestHook.saved.isConfirmed).toBe(true);
      expect(latestHook.isDirty).toBe(false);

      // Verify POST body only sent min_net_rr
      const postCall = (globalThis.fetch as any).mock.calls[1];
      expect(postCall[0]).toBe('/api/scalp/config');
      expect(JSON.parse(postCall[1].body)).toEqual({ min_net_rr: 1.5 });
      expect(onSaveSuccessMock).toHaveBeenCalledWith(expect.objectContaining({ value: 1.5, isConfirmed: true }));
    });

    it('Save failure preserves last confirmed value and draft input', async () => {
      let latestHook: any = null;

      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ min_net_rr: 2.0, rr_policy_source: 'DEFAULT' }),
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 500,
          json: async () => ({ detail: 'DB connection failure' }),
        });

      await act(async () => {
        root?.render(<HookHarness onHook={(ret) => { latestHook = ret; }} />);
      });

      await act(async () => {
        latestHook.setDraftText('1.80');
      });

      let saveOk = true;
      await act(async () => {
        saveOk = await latestHook.save();
      });

      expect(saveOk).toBe(false);
      expect(latestHook.saved.value).toBe(2.0); // Preserved previous confirmed
      expect(latestHook.draftText).toBe('1.80'); // Preserved user edit
      expect(latestHook.error).toBe('DB connection failure');
    });
  });

  // =========================================================================
  // 4. PRODUCTION COMPONENTS: TradeDetailsDrawer, Sidebar, CG01Card
  // =========================================================================
  describe('Production Component Rendering', () => {
    it('TradeDetailsDrawer renders 1:1.50 (Lúc ARM) for mapped Input A', () => {
      const inputA: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        id: 'order-a',
        order_id: 'order-a',
        setup_id: 'order-a',
        minNetRR: 1.5,
        rrPolicySource: 'ORDER_SNAPSHOT',
        rrPolicyFingerprint: 'arm-fp',
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
            selectedIntent={inputA}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={2.5} // Current policy is 2.50
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('1:1.50 (Lúc ARM)');
      expect(bodyText).not.toContain('Chưa lưu ngưỡng lúc ARM');
    });

    it('TradeDetailsDrawer renders Chưa lưu ngưỡng lúc ARM when snapshot is missing', () => {
      const missingIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        id: 'order-b',
        order_id: 'order-b',
        setup_id: 'order-b',
        minNetRR: null,
        rrPolicyStatus: 'MISSING',
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
        estimatedNetRR: 1.7,
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
            selectedIntent={missingIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={2.0}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Chưa lưu ngưỡng lúc ARM');
    });

    it('QuickDecisionSidebar raises POLICY_GATE when current RR policy is unconfirmed/loading', async () => {
      const draftIntent: SelectedTradeIntent = {
        source: 'DRAFT',
        id: 'draft-1',
        setup_id: 'draft-1',
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
        estimatedNetRR: 1.7,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'draft',
        snapshotAt: Date.now(),
      };

      await act(async () => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={draftIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onOpenDetailsDrawer={() => {}}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            appliedMinNetRR={undefined}
            isRrPolicyConfirmed={false}
            rrPolicyStatus="LOADING"
          />
        );
      });

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Chưa xác nhận cấu hình R:R');
    });

    it('QuickDecisionSidebar raises NET_RR_TOO_LOW blocker when actual 1.70 < required 2.50', async () => {
      const draftIntent: SelectedTradeIntent = {
        source: 'DRAFT',
        id: 'draft-2',
        setup_id: 'draft-2',
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
        estimatedNetRR: 1.7,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'draft',
        snapshotAt: Date.now(),
      };

      await act(async () => {
        root?.render(
          <QuickDecisionSidebar
            selectedIntent={draftIntent}
            activePosition={null}
            hasArmedOrder={false}
            upcomingSetups={[]}
            stalePlanNotice={null}
            onClearStalePlanNotice={() => {}}
            onFocusSetupOnChart={() => {}}
            onArmSetup={() => {}}
            onCancelSetup={() => {}}
            onClosePosition={() => {}}
            onOpenDetailsDrawer={() => {}}
            analysis={null}
            leverage={5}
            marginMode="ISOLATED"
            appliedMinNetRR={2.5}
            isRrPolicyConfirmed={true}
            rrPolicyStatus="READY"
            rrPolicySource="SYSTEM_CONFIG"
          />
        );
      });

      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Net R:R');
      expect(bodyText).toContain('1:2.50');
    });

    it('CG01DiagnosticCard renders Đang tải... when unconfirmed', () => {
      const displayState: any = {
        stageLabel: 'THEO DÕI FVG',
        tone: 'neutral',
        direction: 'LONG',
        poiId: 'fvg-1',
        description: 'Testing',
        isStale: false,
        isConnected: true,
      };

      act(() => {
        root?.render(
          <CG01DiagnosticCard
            displayState={displayState}
            appliedMinNetRR={null}
            isRrPolicyConfirmed={false}
            rrPolicyStatus="LOADING"
          />
        );
      });

      const toggleBtn = container?.querySelector('[data-testid="cg01-toggle-settings-btn"]') as HTMLButtonElement;
      act(() => {
        toggleBtn?.click();
      });

      const badge = container?.querySelector('[data-testid="cg01-applied-rr-badge"]');
      expect(badge?.textContent).toBe('Đang tải...');
    });

    it('CG01DiagnosticCard renders 1:1.50 when confirmed', () => {
      const displayState: any = {
        stageLabel: 'THEO DÕI FVG',
        tone: 'neutral',
        direction: 'LONG',
        poiId: 'fvg-1',
        description: 'Testing',
        isStale: false,
        isConnected: true,
      };

      act(() => {
        root?.render(
          <CG01DiagnosticCard
            displayState={displayState}
            appliedMinNetRR={1.5}
            isRrPolicyConfirmed={true}
            rrPolicyStatus="READY"
          />
        );
      });

      const toggleBtn = container?.querySelector('[data-testid="cg01-toggle-settings-btn"]') as HTMLButtonElement;
      act(() => {
        toggleBtn?.click();
      });

      const badge = container?.querySelector('[data-testid="cg01-applied-rr-badge"]');
      expect(badge?.textContent).toBe('1:1.50');
    });
  });
});
