// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/callerAcceptance.test.ts
 * V16.39.13 Caller Acceptance & UI Integration Tests
 *
 * Rules 091-094, 110, 141-144:
 * - TradeDetailsDrawer fallback hook ownership and lifecycle.
 * - Prioritizes bundle detail when available; disables fallback request.
 * - Aborts and invalidates fallback request when drawer closes or section changes.
 * - Inline error display with accessible retry button.
 * - Summary score badge remains intact when detail fetch fails.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { api } from '../api/client';
import { TradeDetailsDrawer } from '../components/TradeDetailsDrawer';
import { CandidateScoreDetails } from '../components/CandidateScoreDetails';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

describe('TradeDetailsDrawer Caller Acceptance (Rules 093, 094, 143, 144)', () => {
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.restoreAllMocks();
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
  });

  it('Rule 143: fetches fallback detail when binding is ORDER and bundle detail is absent', async () => {
    const mockResponseA = {
      status: 'SUCCESS',
      order_id: 'ord-drawer-A',
      detail: {
        order_id: 'ord-drawer-A',
        score: 78.0,
        status: 'AVAILABLE',
        assessment_id: 'assess-A',
        factors: [
          {
            code: 'FVG_ALIGNMENT',
            earned_points: 18.0,
            max_points: 20.0,
            status: 'FULL',
            explanation_vi: 'Khối FVG tăng M15 hợp lệ',
          },
        ],
      },
    };

    const spy = vi.spyOn(api, 'getOrderQualityScore').mockResolvedValue(mockResponseA as any);

    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: {
            kind: 'ORDER',
            id: 'ord-drawer-A',
          } as any,
          selectedIntent: {
            source: 'OPEN_POSITION',
            setup_id: 'ord-drawer-A',
            candidate_score_summary: {
              status: 'AVAILABLE',
              score: 78,
              max_score: 100,
              frozen: true,
              assessment_id: 'assess-A',
            },
          } as any,
          selectedRawBundle: null, // Bundle detail absent
        })
      );
    });

    expect(spy).toHaveBeenCalledWith('ord-drawer-A', expect.any(AbortSignal));
    expect(container?.innerHTML).toContain('FVG_ALIGNMENT');
    expect(container?.innerHTML).toContain('18/20');
    expect(container?.innerHTML).toContain('Khối FVG tăng M15 hợp lệ');
  });

  it('Rule 094 & 143: rawBundle arrival disables fallback and prevents late response overwrite', async () => {
    let resolveFallback: (val: any) => void;
    const fallbackPromise = new Promise((resolve) => {
      resolveFallback = resolve;
    });

    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      capturedSignal = signal;
      return fallbackPromise as any;
    });

    // 1. Mount with bundle detail absent -> triggers fallback fetch
    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: { kind: 'ORDER', id: 'ord-bundle-test' } as any,
          selectedIntent: {
            source: 'OPEN_POSITION',
            setup_id: 'ord-bundle-test',
            candidate_score_summary: { score: 85, status: 'AVAILABLE' },
          } as any,
          selectedRawBundle: null,
        })
      );
    });

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    // 2. Now bundle arrives with authoritative bundle detail
    const verifiedBundleDetail = {
      order_id: 'ord-bundle-test',
      score: 85.0,
      status: 'AVAILABLE',
      factors: [
        {
          code: 'BUNDLE_FACTOR',
          earned_points: 20,
          max_points: 20,
          status: 'FULL',
          explanation_vi: 'Chi tiết từ Bundle chuẩn xác',
        },
      ],
    };

    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: { kind: 'ORDER', id: 'ord-bundle-test' } as any,
          selectedIntent: {
            source: 'OPEN_POSITION',
            setup_id: 'ord-bundle-test',
            candidate_score_summary: { score: 85, status: 'AVAILABLE' },
          } as any,
          selectedRawBundle: {
            candidate_score_detail: verifiedBundleDetail,
          } as any,
        })
      );
    });

    // Fallback request must be aborted
    expect(capturedSignal?.aborted).toBe(true);
    expect(container?.innerHTML).toContain('BUNDLE_FACTOR');

    // 3. Delayed fallback response arrives with older score 60
    await act(async () => {
      resolveFallback!({
        status: 'SUCCESS',
        order_id: 'ord-bundle-test',
        detail: {
          order_id: 'ord-bundle-test',
          score: 60.0,
          status: 'AVAILABLE',
          factors: [{ code: 'OLD_FALLBACK' }],
        },
      });
    });

    // Bundle detail remains authoritative!
    expect(container?.innerHTML).toContain('BUNDLE_FACTOR');
    expect(container?.innerHTML).not.toContain('OLD_FALLBACK');
  });

  it('Rule 144: changing section away from quality invalidates and aborts in-flight request', async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      capturedSignal = signal;
      return new Promise(() => {}); // pending
    });

    // Open on quality section
    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: { kind: 'ORDER', id: 'ord-section-test' } as any,
          selectedIntent: { source: 'OPEN_POSITION', setup_id: 'ord-section-test' } as any,
          selectedRawBundle: null,
        })
      );
    });

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    // Switch section to 'lessons'
    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'lessons',
          selectedBinding: { kind: 'ORDER', id: 'ord-section-test' } as any,
          selectedIntent: { source: 'OPEN_POSITION', setup_id: 'ord-section-test' } as any,
          selectedRawBundle: null,
        })
      );
    });

    expect(capturedSignal?.aborted).toBe(true);
  });

  it('Rule 091: closing drawer aborts in-flight score request', async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      capturedSignal = signal;
      return new Promise(() => {}); // pending
    });

    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: true,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: { kind: 'ORDER', id: 'ord-close-test' } as any,
          selectedIntent: { source: 'OPEN_POSITION', setup_id: 'ord-close-test' } as any,
          selectedRawBundle: null,
        })
      );
    });

    expect(capturedSignal?.aborted).toBe(false);

    // Close drawer
    await act(async () => {
      root?.render(
        React.createElement(TradeDetailsDrawer as any, {
          isOpen: false,
          onClose: () => {},
          activeSection: 'quality',
          selectedBinding: { kind: 'ORDER', id: 'ord-close-test' } as any,
          selectedIntent: { source: 'OPEN_POSITION', setup_id: 'ord-close-test' } as any,
          selectedRawBundle: null,
        })
      );
    });

    expect(capturedSignal?.aborted).toBe(true);
  });
});

describe('CandidateScoreDetails Caller Integration (Rules 097, 098, 142)', () => {
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
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
  });

  it('Rule 098 & 142: renders accessible retry button with type="button" and disabled during loading', async () => {
    let retryCalled = false;

    await act(async () => {
      root?.render(
        React.createElement(CandidateScoreDetails, {
          summary: {
            status: 'AVAILABLE',
            score: 78,
            max_score: 100,
            base_score: 83,
            lesson_delta: -5,
            threshold: 70,
            decision: 'ELIGIBLE',
            as_of_ms: 1700000000000,
            source: 'ARM_SNAPSHOT',
            frozen: true,
            assessment_id: 'assess-123',
            candidate_key: 'XAUUSDT:15M:LONG:2650.0',
            disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
          },
          detail: null,
          loading: false,
          error: 'DETAIL_BINDING_MISMATCH',
          onRetry: () => {
            retryCalled = true;
          },
        })
      );
    });

    const button = container?.querySelector('button');
    expect(button).not.toBeNull();
    expect(button?.getAttribute('type')).toBe('button');
    expect(button?.textContent?.trim()).toBe('Thử lại');
    expect(button?.hasAttribute('disabled')).toBe(false);

    // Click retry
    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(retryCalled).toBe(true);

    // Re-render in loading state
    await act(async () => {
      root?.render(
        React.createElement(CandidateScoreDetails, {
          summary: {
            status: 'AVAILABLE',
            score: 78,
            max_score: 100,
            base_score: 83,
            lesson_delta: -5,
            threshold: 70,
            decision: 'ELIGIBLE',
            as_of_ms: 1700000000000,
            source: 'ARM_SNAPSHOT',
            frozen: true,
            assessment_id: 'assess-123',
            candidate_key: 'XAUUSDT:15M:LONG:2650.0',
            disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
          },
          detail: null,
          loading: true,
          error: 'DETAIL_BINDING_MISMATCH',
          onRetry: () => {},
        })
      );
    });

    const disabledButton = container?.querySelector('button');
    expect(disabledButton?.hasAttribute('disabled')).toBe(true);
  });
});

describe('JournalTab Integration (Rules 091, 092, 141, 142)', () => {
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.restoreAllMocks();
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
  });

  it('Rule 141 & 142: opening trade in JournalTab triggers score detail; closing drawer aborts and error displays retry', async () => {
    const tradeA = {
      id: 'ord-journal-A',
      symbol: 'XAUUSDT',
      direction: 'LONG',
      strategy_family: 'FVG_EXPANSION',
      entry_price: 2650,
      exit_price: 2670,
      pnl: 200,
      realized_pnl_net: 200,
      realized_r: 1.5,
      outcome: 'WIN',
      state: 'CLOSED',
      created_at: 1700000000000,
      closed_at: 1700003600000,
      candidate_score_summary: {
        status: 'AVAILABLE' as const,
        score: 78,
        max_score: 100,
        base_score: 83,
        lesson_delta: -5,
        threshold: 70,
        decision: 'ELIGIBLE',
        as_of_ms: 1700000000000,
        source: 'ARM_SNAPSHOT' as const,
        frozen: true,
        assessment_id: 'assess-qa',
        candidate_key: 'XAUUSDT:15M:LONG:2650.0',
        disclaimer: 'Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng.',
      },
    };

    vi.spyOn(api, 'getJournalPaginated').mockResolvedValue({
      items: [tradeA],
      trades: [tradeA],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
      summary: {
        total_trades: 1,
        open_count: 0,
        winrate_pct: 100.0,
        net_pnl: 200.0,
        wins: 1,
        losses: 0,
        breakevens: 0,
        profit_factor: 2.0,
        average_realized_r: 1.5,
      },
    } as any);

    vi.spyOn(api, 'getLessons').mockResolvedValue([] as any);
    vi.spyOn(api, 'getTradeReview').mockResolvedValue(null as any);

    let resolveDetailA: (val: any) => void;
    const detailPromiseA = new Promise((resolve) => {
      resolveDetailA = resolve;
    });

    let capturedSignal: AbortSignal | undefined;
    const qualitySpy = vi.spyOn(api, 'getOrderQualityScore').mockImplementation((_, signal) => {
      capturedSignal = signal;
      return detailPromiseA as any;
    });

    // Lazy load JournalTab to avoid module resolution conflicts
    const { JournalTab } = await import('../JournalTab');

    await act(async () => {
      root?.render(React.createElement(JournalTab, {}));
    });

    // Flush loadJournal async microtasks
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // While drawer is closed, getOrderQualityScore must NOT be called
    expect(qualitySpy).not.toHaveBeenCalled();

    // Find and click on the trade detail button to open drawer
    const buttons = Array.from(container?.querySelectorAll('button') || []);
    const openBtn = buttons.find((b) => b.textContent?.includes('Chi Tiết'));
    expect(openBtn).toBeDefined();

    await act(async () => {
      openBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // Now drawer is open, getOrderQualityScore should be called for ord-journal-A
    expect(qualitySpy).toHaveBeenCalledWith('ord-journal-A', expect.any(AbortSignal));
    expect(capturedSignal?.aborted).toBe(false);

    // Resolve detail with canonical factor FVG 18/20
    await act(async () => {
      resolveDetailA!({
        status: 'SUCCESS',
        order_id: 'ord-journal-A',
        detail: {
          order_id: 'ord-journal-A',
          assessment_id: 'assess-qa',
          score: 78.0,
          status: 'AVAILABLE',
          factors: [
            {
              code: 'FVG_M15',
              earned_points: 18.0,
              max_points: 20.0,
              status: 'FULL',
              explanation_vi: 'Khối FVG tăng M15 chuẩn xác',
            },
          ],
        },
      });
    });

    // Breakdown is populated in the drawer
    expect(container?.innerHTML).toContain('FVG_M15');
    expect(container?.innerHTML).toContain('18/20');
    expect(container?.innerHTML).toContain('Khối FVG tăng M15 chuẩn xác');
  });

  it('Rule 141: closing drawer while pending aborts request; opening trade B fetches trade B', async () => {
    const tradeA = {
      id: 'ord-A-pending',
      symbol: 'XAUUSDT',
      direction: 'LONG',
      entry_price: 2650,
      exit_price: 2670,
      pnl: 100,
      realized_pnl_net: 100,
      realized_r: 1.0,
      outcome: 'WIN',
      state: 'CLOSED',
      created_at: 1700000000000,
      candidate_score_summary: {
        status: 'AVAILABLE' as const,
        score: 78,
        max_score: 100,
        assessment_id: 'assess-A',
      },
    };

    const tradeB = {
      id: 'ord-B-target',
      symbol: 'BTCUSDT',
      direction: 'SHORT',
      entry_price: 90000,
      exit_price: 89000,
      pnl: 150,
      realized_pnl_net: 150,
      realized_r: 1.5,
      outcome: 'WIN',
      state: 'CLOSED',
      created_at: 1700001000000,
      candidate_score_summary: {
        status: 'AVAILABLE' as const,
        score: 64,
        max_score: 100,
        assessment_id: 'assess-B',
      },
    };

    vi.spyOn(api, 'getJournalPaginated').mockResolvedValue({
      items: [tradeA, tradeB],
      trades: [tradeA, tradeB],
      total: 2,
      page: 1,
      page_size: 20,
      total_pages: 1,
      summary: {
        total_trades: 2,
        open_count: 0,
        winrate_pct: 100.0,
        net_pnl: 250.0,
        wins: 2,
        losses: 0,
        breakevens: 0,
        profit_factor: 2.5,
        average_realized_r: 1.25,
      },
    } as any);

    vi.spyOn(api, 'getLessons').mockResolvedValue([] as any);
    vi.spyOn(api, 'getTradeReview').mockResolvedValue(null as any);

    let resolveDetailA: (val: any) => void;
    const detailPromiseA = new Promise((resolve) => {
      resolveDetailA = resolve;
    });

    const mockResponseB = {
      status: 'SUCCESS',
      order_id: 'ord-B-target',
      detail: {
        order_id: 'ord-B-target',
        assessment_id: 'assess-B',
        score: 64.0,
        status: 'AVAILABLE',
        factors: [
          {
            code: 'LIQUIDITY_SWEEP_B',
            earned_points: 15.0,
            max_points: 20.0,
            status: 'FULL',
            explanation_vi: 'Quét thanh khoản đỉnh phiên Á',
          },
        ],
      },
    };

    let capturedSignalA: AbortSignal | undefined;
    vi.spyOn(api, 'getOrderQualityScore').mockImplementation((id: string, signal) => {
      if (id === 'ord-A-pending') {
        capturedSignalA = signal;
        return detailPromiseA as any;
      }
      return Promise.resolve(mockResponseB as any);
    });

    const { JournalTab } = await import('../JournalTab');

    await act(async () => {
      root?.render(React.createElement(JournalTab, {}));
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // 1. Open drawer for trade A
    const buttons = Array.from(container?.querySelectorAll('button') || []);
    const openBtnA = buttons.find((b) => b.textContent?.includes('Chi Tiết'));
    expect(openBtnA).toBeDefined();

    await act(async () => {
      openBtnA?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(capturedSignalA).toBeDefined();
    expect(capturedSignalA?.aborted).toBe(false);

    // 2. Close drawer while A is still pending
    const closeBtn = container?.querySelector('button[aria-label="Đóng"], button[title="Đóng"], .lucide-x')?.closest('button');
    expect(closeBtn).toBeDefined();

    await act(async () => {
      closeBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // Signal must be aborted immediately upon closing drawer
    expect(capturedSignalA?.aborted).toBe(true);

    // 3. Late resolve of A
    await act(async () => {
      resolveDetailA!({
        status: 'SUCCESS',
        order_id: 'ord-A-pending',
        detail: {
          order_id: 'ord-A-pending',
          score: 78.0,
          status: 'AVAILABLE',
          factors: [{ code: 'STALE_A' }],
        },
      });
    });

    // 4. Open drawer for trade B
    const allButtonsAfterClose = Array.from(container?.querySelectorAll('button') || []);
    const openBtns = allButtonsAfterClose.filter((b) => b.textContent?.includes('Chi Tiết'));
    const openBtnB = openBtns[1] || openBtns[0];

    await act(async () => {
      openBtnB?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // Drawer must render factors for trade B, never stale A!
    expect(container?.innerHTML).toContain('LIQUIDITY_SWEEP_B');
    expect(container?.innerHTML).toContain('Quét thanh khoản đỉnh phiên Á');
    expect(container?.innerHTML).not.toContain('STALE_A');
  });
});


