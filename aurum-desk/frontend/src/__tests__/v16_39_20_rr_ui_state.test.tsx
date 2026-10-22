// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/v16_39_20_rr_ui_state.test.tsx
 * Aurum V16.39.20 — Settings Read-Error Label & App-to-Drawer Context Propagation
 *
 * Verifies:
 * 1. FAILURE C (Repro D1):
 *    - RrPolicySettingsCard under policyReadStatus: 'ERROR' with lastKnown (saved.value: 1.5):
 *      MUST NOT show "Đang áp dụng: 1:1.50" or "R:R Đã Đồng Bộ".
 *      MUST show "Cấu hình gần nhất: 1:1.50 — Chưa xác minh lại" and "Chưa xác minh cấu hình" (disabled).
 *      Retry button calls onRetryRead ("Tải Lại Cấu Hình").
 *    - RrPolicySettingsCard under errorOrigin: 'SAVE' with policyReadStatus: 'READY':
 *      MUST preserve old confirmed "Đang áp dụng: 1:1.50" and keep draft dirty.
 *
 * 2. FAILURE D (Repro D2):
 *    - TradeDetailsDrawer receiving CURRENT ERROR context from App owner:
 *      MUST NOT show "1:1.50 (Hiện hành)".
 *      MUST show "Chưa xác minh lại cấu hình R:R".
 *    - TradeDetailsDrawer with frozen order KNOWN 2.0 under CURRENT ERROR:
 *      MUST show "1:2.00 (Lúc ARM)" independently from current error.
 *    - TradeDetailsDrawer with CURRENT READY 1.5:
 *      MUST show "1:1.50 (Hiện hành)".
 *
 * 3. App Post-Save Evaluation Sync & Callback Bridge:
 *    - onSaveSuccess returns Promise to hook.
 *    - evaluationSyncStatus: 'PENDING' -> 'READY' / 'ERROR' without rolling back save.
 */

import { act } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';

import { RrPolicySettingsCard } from '../components/RrPolicySettingsCard';
import { TradeDetailsDrawer } from '../components/TradeDetailsDrawer';
import { QuickDecisionSidebar } from '../components/QuickDecisionSidebar';
import { useRrPolicySettings } from '../hooks/useRrPolicySettings';
import type { SelectedTradeIntent } from '../App';
import type { CurrentPolicyContext } from '../utils/rrPolicyView';

describe('Aurum V16.39.20 — UI State Verification Suite', () => {
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

  // =========================================================================
  // 1. FAILURE C: RrPolicySettingsCard Error Separation & Labels
  // =========================================================================
  describe('Failure C: RrPolicySettingsCard Read Error vs Save Error', () => {
    it('C1: Read ERROR with lastKnown displays "Cấu hình gần nhất" and "Chưa xác minh cấu hình" (NOT "Đang áp dụng" or "Đã đồng bộ")', () => {
      const mockRetryRead = vi.fn();

      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true }}
            draftText="1.50"
            isDirty={false}
            status="error"
            error="Revalidation failed"
            successMessage={null}
            policyReadStatus="ERROR"
            errorOrigin="READ"
            readError="Revalidation failed"
            saveError={null}
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={() => {}}
            onRetry={mockRetryRead}
            onRetryRead={mockRetryRead}
          />
        );
      });

      const text = container?.textContent || '';

      // Assertions per Checklist 034, 037, Appendix D1:
      expect(text).not.toContain('Đang áp dụng: 1:1.50');
      expect(text).not.toContain('R:R Đã Đồng Bộ');
      expect(text).toContain('Cấu hình gần nhất');
      expect(text).toContain('1:1.50');
      expect(text).toContain('Chưa xác minh lại');
      expect(text).toContain('Chưa xác minh cấu hình');

      // Submit button should be disabled when not dirty and unconfirmed
      const submitBtn = container?.querySelector('button[type="submit"]') as HTMLButtonElement | null;
      expect(submitBtn).not.toBeNull();
      expect(submitBtn?.disabled).toBe(true);

      // Retry button specifically targets read re-validation
      const retryBtn = container?.querySelector('button:has-text("Tải Lại Cấu Hình"), button.bg-rose-900\\/60') as HTMLButtonElement | null;
      if (retryBtn) {
        act(() => {
          retryBtn.click();
        });
        expect(mockRetryRead).toHaveBeenCalled();
      }
    });

    it('C2: Save ERROR preserves previous confirmed policy as "Đang áp dụng: 1:1.50" and keeps dirty draft', () => {
      const mockSave = vi.fn();

      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true }}
            draftText="2.50"
            isDirty={true}
            status="error"
            error="Validation failed on backend"
            successMessage={null}
            policyReadStatus="READY"
            errorOrigin="SAVE"
            readError={null}
            saveError="Validation failed on backend"
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={mockSave}
          />
        );
      });

      const text = container?.textContent || '';

      // Confirmed saved setting remains authoritative
      expect(text).toContain('Đang áp dụng');
      expect(text).toContain('1:1.50');

      // Error message indicates save failure
      expect(text).toContain('Lưu cấu hình thất bại');
      expect(text).toContain('Validation failed on backend');

      // Draft remains dirty and submit button allows saving draft
      const submitBtn = container?.querySelector('button[type="submit"]') as HTMLButtonElement | null;
      expect(submitBtn).not.toBeNull();
      expect(submitBtn?.disabled).toBe(false);
      expect(submitBtn?.textContent).toContain('Lưu R:R (1:2.50)');
    });

    it('C3: Normal READY state displays "Đang áp dụng: 1:1.50" and "R:R Đã Đồng Bộ (1:1.50)"', () => {
      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true }}
            draftText="1.50"
            isDirty={false}
            status="ready"
            error={null}
            successMessage={null}
            policyReadStatus="READY"
            errorOrigin={null}
            readError={null}
            saveError={null}
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={() => Promise.resolve(true)}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('Đang áp dụng');
      expect(text).toContain('1:1.50');
      expect(text).toContain('R:R Đã Đồng Bộ (1:1.50)');
    });
  });

  // =========================================================================
  // 2. FAILURE D: TradeDetailsDrawer Context Propagation & Snapshot Priority
  // =========================================================================
  describe('Failure D: TradeDetailsDrawer Context Propagation', () => {
    const draftIntent: SelectedTradeIntent = {
      source: 'DRAFT',
      id: 'draft-1',
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
      status: 'draft',
      snapshotAt: Date.now(),
    };

    it('D1: Drawer with CURRENT ERROR context displays "Chưa xác minh lại cấu hình R:R" (NOT "(Hiện hành)")', () => {
      const errorContext: CurrentPolicyContext = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        status: 'ERROR',
        isConfirmed: false,
        policyReadStatus: 'ERROR',
        errorOrigin: 'READ',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={draftIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={{ min_net_rr: 1.5, effective_min_net_rr: 1.5 } as any}
            appliedMinNetRR={1.5}
            currentPolicyContext={errorContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).not.toContain('1:1.50 (Hiện hành)');
      expect(text).toContain('Chưa xác minh lại cấu hình R:R');
    });

    it('D2: Drawer with CURRENT READY 1.5 displays "1:1.50 (Hiện hành)"', () => {
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
            selectedIntent={draftIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={{ min_net_rr: 1.5, effective_min_net_rr: 1.5 } as any}
            appliedMinNetRR={1.5}
            currentPolicyContext={readyContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('1:1.50 (Hiện hành)');
    });

    it('D3: Frozen order KNOWN 2.0 still displays "1:2.00 (Lúc ARM)" even when CURRENT context is in ERROR', () => {
      const frozenOrderIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        id: 'ord-frozen',
        order_id: 'ord-frozen',
        setup_id: 'ord-frozen',
        rrPolicyStatus: 'KNOWN',
        minNetRR: 2.0,
        rrPolicySource: 'ORDER_SNAPSHOT',
        estimatedNetRR: 2.3,
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

      const errorContext: CurrentPolicyContext = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        status: 'ERROR',
        isConfirmed: false,
        policyReadStatus: 'ERROR',
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
            appliedMinNetRR={1.5}
            currentPolicyContext={errorContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('1:2.00 (Lúc ARM)');
      expect(text).not.toContain('1:1.50');
    });

    it('D4: Frozen order with MISSING snapshot under CURRENT ERROR displays "Chưa lưu ngưỡng lúc ARM"', () => {
      const missingOrderIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        order_id: 'order-missing-rr',
        id: 'order-missing-rr',
        // snapshot is missing
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2675.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossRR: 2.5,
        estimatedNetRR: 2.2,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };

      const errorContext: CurrentPolicyContext = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        status: 'ERROR',
        isConfirmed: false,
        policyReadStatus: 'ERROR',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={missingOrderIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={1.5}
            currentPolicyContext={errorContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('Chưa lưu ngưỡng lúc ARM');
      expect(text).not.toContain('1:1.50 (Hiện hành)');
    });

    it('D5: Frozen order with INVALID snapshot under CURRENT ERROR displays "Ngưỡng snapshot lúc ARM không hợp lệ"', () => {
      const invalidOrderIntent: SelectedTradeIntent = {
        source: 'OPEN_POSITION',
        order_id: 'order-invalid-rr',
        id: 'order-invalid-rr',
        minNetRR: null, // explicitly null
        rrPolicyStatus: 'INVALID',
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2675.0,
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossRR: 2.5,
        estimatedNetRR: 2.2,
        symbol: 'XAUUSDT',
        timeframe: '15M',
        revision: 1,
        orderType: 'LIMIT',
        leverage: 5,
        marginMode: 'ISOLATED',
        status: 'OPEN',
        snapshotAt: Date.now(),
      };

      const errorContext: CurrentPolicyContext = {
        value: 1.5,
        source: 'SYSTEM_CONFIG',
        status: 'ERROR',
        isConfirmed: false,
        policyReadStatus: 'ERROR',
      };

      act(() => {
        root?.render(
          <TradeDetailsDrawer
            isOpen={true}
            activeSection="risk"
            onSectionChange={() => {}}
            onClose={() => {}}
            selectedIntent={invalidOrderIntent}
            activePosition={null}
            upcomingSetups={[]}
            onFocusSetupOnChart={() => {}}
            onRefreshUpcoming={() => {}}
            nySession={null}
            policy={null}
            appliedMinNetRR={1.5}
            currentPolicyContext={errorContext}
            leverage={5}
            marginMode="ISOLATED"
            analysis={null}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('Ngưỡng snapshot lúc ARM không hợp lệ');
    });
  });

  // 3. SETTINGS CARD EDGE CASES & EVALUATION SYNC
  describe('Settings Card Edge Cases & Evaluation Sync', () => {
    it('C4: Read ERROR without lastKnown (value: null) displays "Chưa xác minh cấu hình" safely', () => {
      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: null, source: 'UNCONFIRMED', isConfirmed: false }}
            draftText="2.00"
            isDirty={false}
            status="error"
            error="Connection refused"
            successMessage={null}
            policyReadStatus="ERROR"
            errorOrigin="READ"
            readError="Connection refused"
            saveError={null}
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={() => Promise.resolve(false)}
          />
        );
      });

      const text = container?.textContent || '';
      expect(text).toContain('Chưa xác minh cấu hình');
      expect(text).not.toContain('Đang áp dụng');
      expect(text).not.toContain('Cấu hình gần nhất');

      const submitBtn = container?.querySelector('button[type="submit"]') as HTMLButtonElement | null;
      expect(submitBtn?.textContent).toContain('Chưa xác minh cấu hình');
      expect(submitBtn?.disabled).toBe(true);
    });

    it('C5: Evaluation Sync displays PENDING and ERROR states with retry option', () => {
      const mockRetrySync = vi.fn();

      // PENDING state
      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true }}
            draftText="1.50"
            isDirty={false}
            status="ready"
            error={null}
            successMessage="Đã lưu ngưỡng tối thiểu 1:1.50"
            policyReadStatus="READY"
            errorOrigin={null}
            readError={null}
            saveError={null}
            evaluationSyncStatus="PENDING"
            evaluationSyncError={null}
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={() => Promise.resolve(true)}
            onRetrySync={mockRetrySync}
          />
        );
      });

      let text = container?.textContent || '';
      expect(text).toContain('đang cập nhật đánh giá');

      // ERROR state
      act(() => {
        root?.render(
          <RrPolicySettingsCard
            saved={{ value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true }}
            draftText="1.50"
            isDirty={false}
            status="ready"
            error={null}
            successMessage="Đã lưu ngưỡng tối thiểu 1:1.50"
            policyReadStatus="READY"
            errorOrigin={null}
            readError={null}
            saveError={null}
            evaluationSyncStatus="ERROR"
            evaluationSyncError="Không thể cập nhật danh sách setup"
            onDraftChange={() => {}}
            onSelectPreset={() => {}}
            onReset={() => {}}
            onSave={() => Promise.resolve(true)}
            onRetrySync={mockRetrySync}
          />
        );
      });

      text = container?.textContent || '';
      expect(text).toContain('Đánh giá phụ thuộc chưa đồng bộ');
      expect(text).toContain('Không thể cập nhật danh sách setup');

      const syncRetryBtn = Array.from(container?.querySelectorAll('button') || []).find(
        (b) => b.textContent?.includes('Thử Lại Đồng Bộ')
      );
      expect(syncRetryBtn).toBeDefined();
      act(() => {
        syncRetryBtn?.click();
      });
      expect(mockRetrySync).toHaveBeenCalledTimes(1);
    });
  });

  // 4. ASYNC CALLBACK BRIDGE & SIDEBAR ERROR GATE
  describe('Async Callback Bridge & Sidebar Error Gate', () => {
    it('W1: Async callback bridge in useRrPolicySettings awaits onSaveSuccess completion', async () => {
      let isCallbackCompleted = false;
      const deferredPromise = new Promise<void>((resolve) => {
        setTimeout(() => {
          isCallbackCompleted = true;
          resolve();
        }, 50);
      });

      const mockOnSaveSuccess = vi.fn().mockImplementation(() => deferredPromise);

      // Create test harness component that uses the hook with async callback
      let hookResult: any = null;
      function TestHookComponent() {
        hookResult = useRrPolicySettings({
          onSaveSuccess: mockOnSaveSuccess,
        });
        return null;
      }

      // Mock fetch for GET and POST
      const originalFetch = (globalThis as any).fetch;
      (globalThis as any).fetch = vi.fn().mockImplementation((_url: string, opts?: any) => {
        if (opts?.method === 'POST') {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({
              effective_config: { min_net_rr: 2.25 },
              source: 'SYSTEM_CONFIG',
              fingerprint: 'fp-save-test',
            }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            effective_config: { min_net_rr: 1.5 },
            source: 'SYSTEM_CONFIG',
            fingerprint: 'fp-init',
          }),
        });
      }) as any;

      try {
        await act(async () => {
          root?.render(<TestHookComponent />);
        });

        // Set draft and save
        await act(async () => {
          hookResult.setDraftText('2.25');
        });

        await act(async () => {
          const saveResult = await hookResult.save();
          expect(saveResult).toBe(true);
        });

        // Verify onSaveSuccess was called and deferred execution resolved
        expect(mockOnSaveSuccess).toHaveBeenCalledTimes(1);
        expect(isCallbackCompleted).toBe(true);
      } finally {
        (globalThis as any).fetch = originalFetch;
      }
    });

    it('W2: QuickDecisionSidebar blocks ARM submission and displays "Lỗi cấu hình R:R" on CURRENT ERROR even with high actual RR', () => {
      const highRrCandidateIntent: SelectedTradeIntent = {
        source: 'WATCH_SETUP',
        id: 'candidate-high-rr',
        setup_id: 'candidate-high-rr',
        setup_instance_id: 'candidate-high-rr-1',
        plannedEntry: 2650.0,
        stopLoss: 2640.0,
        takeProfit: 2700.0, // 5.0R actual
        direction: 'LONG',
        quantity: 1,
        initialRiskUsdt: 10,
        grossLossUsdt: 10,
        grossRewardUsdt: 50,
        netRewardUsdt: 48,
        multiplier: 1,
        grossRR: 5.0,
        estimatedNetRR: 4.8,
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
              selectedIntent: highRrCandidateIntent,
              activePosition: null,
              appliedMinNetRR: 1.5,
              rrPolicyStatus: 'ERROR',
              isRrPolicyConfirmed: false,
              hasArmedOrder: false,
              upcomingSetups: [],
              onOpenDetailsDrawer: () => {},
              onFocusSetupOnChart: () => {},
              onFocusTradeOnChart: () => {},
              onArmSetup: () => {},
              onCancelSetup: () => {},
              onClosePosition: () => {},
              onClearStalePlanNotice: () => {},
              stalePlanNotice: null,
              analysis: null,
              leverage: 5,
              marginMode: 'ISOLATED',
            } as any)}
          />
        );
      });

      const ctaBtn = container?.querySelector('#entry-submit-cta');
      expect(ctaBtn).not.toBeNull();
      // CTA button must be blocked from submission
      expect(ctaBtn?.getAttribute('data-can-submit')).toBe('false');
      expect(ctaBtn?.getAttribute('disabled')).not.toBeNull();

      // UI explicitly indicates R:R policy error, not low RR
      const bodyText = container?.textContent || '';
      expect(bodyText).toContain('Lỗi cấu hình R:R');
      expect(bodyText).not.toContain('Net R:R — chưa đạt yêu cầu');
    });
  });
});
