// @vitest-environment happy-dom
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { usePaperOpportunityMonitor } from '../hooks/usePaperOpportunityMonitor';
import { api } from '../api/client';
import type { MonitorFilterState } from '../types/paperOpportunityMonitor';

describe('Aurum V17.0 — usePaperOpportunityMonitor Hook', () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  const defaultFilters: MonitorFilterState = {
    period: 'today',
    session: 'all',
    status: 'all',
  };

  const mockSummary = {
    schema_version: '1.0',
    generated_at_ms: 1700000000000,
    query_scope: { symbol: 'XAUUSDT', scope: 'CG01' },
    status: 'READY' as const,
    counts: {
      total_unique_opportunities: 2,
      zones_detected: 2,
      zones_watched: 2,
      zones_touched: 2,
      trigger_confirmed: 1,
      plans_valid: 1,
      guard_blocked: 0,
      armed: 1,
      filled: 1,
      expired: 0,
      invalidated: 0,
    },
    top_reasons: [],
    financial: {
      metric_scope: 'RUN_WINDOW',
      orders_count: 1,
      closed_trades_count: 1,
      verified_closed_count: 1,
      wins_count: 1,
      losses_count: 0,
      breakevens_count: 0,
      net_realized_pnl_usd: 15.0,
      verified_net_pnl_subtotal_usd: 15.0,
      winrate_pct: 100.0,
      expectancy_usd_per_trade: 15.0,
      accounting_status: 'COMPLETE' as const,
    },
    coverage: { run_id: 'run-1', run_status: 'COLLECTING', symbol: 'XAUUSDT', scope: 'CG01' },
    limitations: [],
  };

  const mockPage = {
    items: [
      {
        key: 'run-1:opp-1',
        opportunity_id: 'opp-1',
        run_id: 'run-1',
        model_id: 'CG01',
        direction: 'LONG' as const,
        session_id: 'run-1',
        session_label_vi: 'run-1',
        first_recorded_at_ms: 1700000000,
        last_observed_at_ms: 1700001000,
        current_stage: 'FILLED',
        stage_label_vi: 'Đã khớp lệnh',
        primary_reason_code: 'WAIT_RETEST',
        primary_reason_vi: 'Chờ giá hồi về',
        classification: 'FILLED',
        data_quality: 'VERIFIED' as const,
        observed_milestone_count: 3,
        score: 85.0,
        score_source: 'TRIGGER_CONFIRMED',
        zone_label: 'FVG_M1',
        chart_target: null,
        limitations: [],
      },
    ],
    total: 1,
    has_more: false,
    next_cursor: null,
    snapshot_as_of_ms: 1700000000000,
    limitations: [],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(mockSummary);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(mockPage);

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

  function TestHarness({
    open,
    filters,
    onHookResult,
  }: {
    open: boolean;
    filters: MonitorFilterState;
    onHookResult?: (res: any) => void;
  }) {
    const hookRes = usePaperOpportunityMonitor({
      open,
      symbol: 'XAUUSDT',
      scope: 'CG01',
      filters,
    });

    if (onHookResult) {
      onHookResult(hookRes);
    }

    return (
      <div data-testid="hook-consumer">
        <span data-testid="summary-status">{hookRes.summary?.status || 'NONE'}</span>
        <span data-testid="page-total">{hookRes.page?.total ?? -1}</span>
        <span data-testid="loading-state">{hookRes.isListLoading ? 'LOADING' : 'IDLE'}</span>
        <button data-testid="select-btn" onClick={() => hookRes.selectOpportunity('opp-1')}>
          Select
        </button>
      </div>
    );
  }

  it('does NOT fetch when open is false', async () => {
    await act(async () => {
      root?.render(<TestHarness open={false} filters={defaultFilters} />);
    });

    expect(api.getOpportunityMonitorSummary).not.toHaveBeenCalled();
    expect(api.getOpportunityMonitorOpportunities).not.toHaveBeenCalled();
  });

  it('fetches summary and page when open is true', async () => {
    await act(async () => {
      root?.render(<TestHarness open={true} filters={defaultFilters} />);
    });

    // Wait for microtasks
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(api.getOpportunityMonitorSummary).toHaveBeenCalledTimes(1);
    expect(api.getOpportunityMonitorOpportunities).toHaveBeenCalledTimes(1);

    const totalEl = container?.querySelector('[data-testid="page-total"]');
    expect(totalEl?.textContent).toBe('1');
  });

  it('fetches detail on selectOpportunity', async () => {
    const mockDetail = {
      item: mockPage.items[0],
      milestones: [],
      order_outcomes: [],
      truncated: false,
      limitations: [],
    };
    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(mockDetail as any);

    let latestHookRes: any = null;

    await act(async () => {
      root?.render(
        <TestHarness
          open={true}
          filters={defaultFilters}
          onHookResult={(res) => {
            latestHookRes = res;
          }}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    await act(async () => {
      latestHookRes?.selectOpportunity('opp-1');
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(api.getOpportunityMonitorDetail).toHaveBeenCalledWith(
      'opp-1',
      expect.objectContaining({ symbol: 'XAUUSDT', scope: 'CG01' }),
      expect.any(AbortSignal)
    );
  });
});
