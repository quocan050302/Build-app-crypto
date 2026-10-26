// @vitest-environment happy-dom
import { act } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';

import { PaperOpportunityMonitorDrawer } from '../components/PaperOpportunityMonitorDrawer';
import { usePaperOpportunityMonitor } from '../hooks/usePaperOpportunityMonitor';
import { api } from '../api/client';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
  MonitorFilterState,
} from '../types/paperOpportunityMonitor';

// @ts-ignore
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const baseSummary: OpportunityMonitorSummary = {
  schema_version: '1.0',
  generated_at_ms: 1700000000000,
  query_scope: {
    symbol: 'XAUUSDT',
    scope: 'CG01',
    run_id: 'run-alpha',
    model_id: null,
    session_id: null,
    start_ms: 1699900000000,
    end_ms: 1700000000000,
  },
  status: 'READY',
  counts: {
    total_unique_opportunities: 1,
    zones_detected: 1,
    zones_watched: 1,
    zones_touched: 1,
    trigger_confirmed: 1,
    plans_valid: 1,
    guard_blocked: 0,
    armed: 0,
    filled: 0,
    expired: 0,
    invalidated: 0,
  },
  top_reasons: [],
  financial: {
    metric_scope: 'RUN_WINDOW',
    orders_count: 0,
    closed_trades_count: 0,
    verified_closed_count: 0,
    wins_count: 0,
    losses_count: 0,
    breakevens_count: 0,
    net_realized_pnl_usd: null,
    verified_net_pnl_subtotal_usd: 0,
    winrate_pct: null,
    expectancy_usd_per_trade: null,
    accounting_status: 'PARTIAL',
  },
  coverage: {
    run_id: 'run-alpha',
    run_status: 'RUNNING',
    symbol: 'XAUUSDT',
    scope: 'CG01',
  },
  limitations: [],
  query_key: 'qk-alpha',
};

const baseListPage: MonitorListPage = {
  items: [
    {
      key: 'run-alpha:opp-1',
      opportunity_id: 'opp-1',
      run_id: 'run-alpha',
      model_id: 'CG01',
      direction: 'LONG',
      session_id: 'run-alpha',
      session_label_vi: 'run-alpha',
      first_recorded_at_ms: 1700000000000,
      last_observed_at_ms: 1700000000000,
      current_stage: 'TRIGGER_CONFIRMED',
      stage_label_vi: 'Xác nhận kích hoạt',
      primary_reason_code: null,
      primary_reason_vi: 'Đã quét thanh khoản',
      classification: null,
      data_quality: 'VERIFIED',
      observed_milestone_count: 1,
      score: 80.0,
      score_source: 'TRIGGER_CONFIRMED',
      zone_label: 'FVG_M1',
      chart_target: {
        kind: 'SETUP',
        id: 'setup-1',
        setup_instance_id: 'inst-1',
        revision: 1,
        verified: true,
      },
      limitations: [],
    },
  ],
  total: 1,
  has_more: false,
  next_cursor: null,
  snapshot_as_of_ms: 1700000000000,
  limitations: [],
  query_key: 'qk-alpha',
  run_id: 'run-alpha',
};

const baseDetail: OpportunityMonitorDetail = {
  item: baseListPage.items[0],
  milestones: [
    {
      id: 'm1',
      stage: 'TRIGGER_CONFIRMED',
      stage_label_vi: 'Xác nhận kích hoạt',
      first_recorded_at_ms: 1700000000000,
      last_updated_at_ms: 1700000000000,
      snapshot_as_of_ms: 1700000000000,
      occurred_at_ms: 1700000000000,
      reason_codes: [],
      reason_summary_vi: 'Xác nhận',
      source: 'FUNNEL_STAGE_SNAPSHOT',
      time_semantics: 'STAGE_SNAPSHOT_TIME',
      evidence_ids: [],
    },
  ],
  plan: {
    entry: 2650.0,
    stop_loss: 2640.0,
    take_profit: 2675.0,
    direction: 'LONG',
    price_source: 'FUNNEL_STAGE_PAYLOAD',
  },
  order_outcomes: [],
  truncated: false,
  limitations: [],
  query_key: 'qk-alpha',
};

describe('Aurum V17.2 — Frontend Reproduction Probes', () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
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
  });

  it('Probe E1: late focus response after Back must NOT trigger chart callback or close drawer', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(baseSummary);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(baseListPage);
    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(baseDetail);

    let resolveChartTarget!: (val: any) => void;
    const chartTargetPromise = new Promise((resolve) => {
      resolveChartTarget = resolve;
    });

    vi.spyOn(api, 'getOpportunityMonitorChartTarget').mockImplementation(() => chartTargetPromise as any);

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={handleClose}
          onFocusSetupOnChart={handleFocusSetup}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // 1. Click row to open detail
    const row = container?.querySelector('[data-testid="monitor-row-opp-1"]') as HTMLElement;
    expect(row).not.toBeNull();
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // 2. Click Focus button (triggers in-flight request)
    const focusBtn = container?.querySelector('[data-testid="monitor-focus-chart-btn"]') as HTMLButtonElement;
    expect(focusBtn).not.toBeNull();
    await act(async () => {
      focusBtn.click();
    });

    // 3. User clicks Back button while focus request is in flight
    const backBtn = container?.querySelector('[data-testid="monitor-back-btn"]') as HTMLButtonElement;
    expect(backBtn).not.toBeNull();
    await act(async () => {
      backBtn.click();
    });

    // 4. Now the delayed chart target response resolves!
    await act(async () => {
      resolveChartTarget({
        status: 'AVAILABLE',
        kind: 'SETUP',
        query_key: 'qk-alpha',
        opportunity_id: 'opp-1',
        target_id: 'setup-1',
        dto: {
          id: 'setup-1',
          version: 1,
          setup_instance_id: 'inst-1',
          symbol: 'XAUUSDT',
          direction: 'LONG',
          state: 'READY',
          planned_entry: 2650.0,
          stop_loss: 2640.0,
          take_profit: 2675.0,
          confirmed_entry: 2650.0,
          confirmed_sl: 2640.0,
          confirmed_tp: 2675.0,
        },
      });
      await new Promise((r) => setTimeout(r, 20));
    });

    // Bug A Assertion: neither onFocusSetupOnChart nor onClose should have been called!
    expect(handleFocusSetup).not.toHaveBeenCalled();
    expect(handleClose).not.toHaveBeenCalled();
  });

  it('Probe E2: handleFocusChart sends expected_revision from chart_target', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(baseSummary);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(baseListPage);
    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(baseDetail);

    const getChartTargetSpy = vi.spyOn(api, 'getOpportunityMonitorChartTarget').mockResolvedValue({
      status: 'AVAILABLE',
      kind: 'SETUP',
      query_key: 'qk-alpha',
      opportunity_id: 'opp-1',
      target_id: 'setup-1',
      dto: {
        id: 'setup-1',
        version: 1,
        setup_instance_id: 'inst-1',
        symbol: 'XAUUSDT',
        direction: 'LONG',
        state: 'READY',
        planned_entry: 2650.0,
        stop_loss: 2640.0,
        take_profit: 2675.0,
        confirmed_entry: 2650.0,
        confirmed_sl: 2640.0,
        confirmed_tp: 2675.0,
      },
    });

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={handleClose}
          onFocusSetupOnChart={handleFocusSetup}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const row = container?.querySelector('[data-testid="monitor-row-opp-1"]') as HTMLElement;
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const focusBtn = container?.querySelector('[data-testid="monitor-focus-chart-btn"]') as HTMLButtonElement;
    await act(async () => {
      focusBtn.click();
    });

    expect(getChartTargetSpy).toHaveBeenCalledWith(
      'opp-1',
      expect.objectContaining({
        expected_target_id: 'setup-1',
        expected_kind: 'SETUP',
        expected_revision: 1,
      }),
      expect.any(AbortSignal)
    );
  });

  it('Probe E5: Hook refuses to publish mismatched summary (run-a) and list (run-b)', async () => {
    const filters: MonitorFilterState = { period: 'today', session: 'all', status: 'all' };

    const summaryA: OpportunityMonitorSummary = {
      ...baseSummary,
      query_scope: { ...baseSummary.query_scope, run_id: 'run-a' },
      coverage: { ...baseSummary.coverage, run_id: 'run-a' },
      query_key: 'qk-run-a',
    };

    const listB: MonitorListPage = {
      ...baseListPage,
      run_id: 'run-b',
      query_key: 'qk-run-b',
      items: [
        {
          ...baseListPage.items[0],
          run_id: 'run-b',
          key: 'run-b:opp-b',
        },
      ],
    };

    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listB);

    let latestResult: any = null;
    function TestComponent() {
      latestResult = usePaperOpportunityMonitor({
        open: true,
        filters,
      });
      return <div>Hook Test</div>;
    }

    await act(async () => {
      root?.render(<TestComponent />);
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    // 1. Hook correctly pinned list request to run-a from summaryA:
    expect(api.getOpportunityMonitorOpportunities).toHaveBeenCalledWith(
      expect.objectContaining({ run_id: 'run-a' }),
      expect.any(AbortSignal)
    );

    // 2. The hook caught the mismatch and refused to publish the corrupted pair:
    expect(latestResult.page).toBeNull();
    expect(latestResult.listError).toMatch(/SCOPE_MISMATCH|RUN_MISMATCH/);
  });
});
