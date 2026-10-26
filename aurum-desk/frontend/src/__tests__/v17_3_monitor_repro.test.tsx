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
  MonitorContextSnapshot,
  MonitorRequestLease,
} from '../types/paperOpportunityMonitor';
import {
  captureMonitorRequestLease,
  isMonitorRequestCurrent,
  assertMonitorDetailIdentity,
  assertMonitorResponseContext,
} from '../utils/paperOpportunityText';

// @ts-ignore
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const summaryA: OpportunityMonitorSummary = {
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

const summaryB: OpportunityMonitorSummary = {
  ...summaryA,
  generated_at_ms: 1700000001000,
  query_scope: {
    ...summaryA.query_scope,
    end_ms: 1700000001000,
  },
  query_key: 'qk-beta',
};

const listA: MonitorListPage = {
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

const listB: MonitorListPage = {
  ...listA,
  query_key: 'qk-beta',
  snapshot_as_of_ms: 1700000001000,
  items: [
    {
      ...listA.items[0],
      key: 'run-alpha:opp-2',
      opportunity_id: 'opp-2',
    },
  ],
};

const detailA: OpportunityMonitorDetail = {
  item: listA.items[0],
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

describe('Aurum V17.3 — Monitor Context Lifecycle Probes', () => {
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
    vi.restoreAllMocks();
  });

  it('Probe R1: Stale focus response resolving after refresh must NOT trigger chart callback or close drawer', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

    // Initial load: summaryA, listA, detailA
    const summarySpy = vi.spyOn(api, 'getOpportunityMonitorSummary')
      .mockResolvedValueOnce(summaryA)
      .mockResolvedValue(summaryB);

    const listSpy = vi.spyOn(api, 'getOpportunityMonitorOpportunities')
      .mockResolvedValueOnce(listA)
      .mockResolvedValue(listB);

    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(detailA);

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

    // 1. Open detail for opp-1
    const row = container?.querySelector('[data-testid="monitor-row-opp-1"]') as HTMLElement;
    expect(row).not.toBeNull();
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // 2. Click Focus button (in-flight request for opp-1 under query_key qk-alpha)
    const focusBtn = container?.querySelector('[data-testid="monitor-focus-chart-btn"]') as HTMLButtonElement;
    expect(focusBtn).not.toBeNull();
    await act(async () => {
      focusBtn.click();
    });

    // 3. User clicks refresh button while focus is in-flight!
    const refreshBtn = container?.querySelector('[data-testid="monitor-refresh-btn"]') as HTMLButtonElement;
    expect(refreshBtn).not.toBeNull();
    await act(async () => {
      refreshBtn.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    // Verify overview moved to summaryB / listB
    expect(summarySpy).toHaveBeenCalledTimes(2);
    expect(listSpy).toHaveBeenCalledTimes(2);

    // 4. Now the delayed chart target response for context A resolves!
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

    // Assertion: onFocusSetupOnChart and onClose must NOT be called because context transitioned!
    expect(handleFocusSetup).not.toHaveBeenCalled();
    expect(handleClose).not.toHaveBeenCalled();
  });

  it('Probe R2: Stale export response resolving after filter change must NOT download file', async () => {
    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listA);

    let resolveReport!: (val: string) => void;
    const reportPromise = new Promise<string>((resolve) => {
      resolveReport = resolve;
    });

    vi.spyOn(api, 'getOpportunityMonitorReportMarkdown').mockImplementation(() => reportPromise as any);

    // Mock createObjectURL and HTMLAnchorElement.prototype.click
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock-url');
    const revokeObjectURLMock = vi.fn();
    const anchorClickMock = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    globalThis.URL.createObjectURL = createObjectURLMock;
    globalThis.URL.revokeObjectURL = revokeObjectURLMock;

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={vi.fn()}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // 1. Click download report button (in-flight)
    const downloadBtn = container?.querySelector('[data-testid="monitor-download-report-btn"]') as HTMLButtonElement;
    expect(downloadBtn).not.toBeNull();
    await act(async () => {
      downloadBtn.click();
    });

    expect(api.getOpportunityMonitorReportMarkdown).toHaveBeenCalledTimes(1);

    // 2. User changes filter to 7d while export is in flight!
    const period7dBtn = container?.querySelector('[data-testid="filter-period-7d"]') as HTMLButtonElement;
    expect(period7dBtn).not.toBeNull();
    await act(async () => {
      period7dBtn.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // 3. Delayed report A resolves
    await act(async () => {
      resolveReport('# Stale Report A Content');
      await new Promise((r) => setTimeout(r, 20));
    });

    // Assertion: ObjectURL should NOT be created, and anchor should NOT be clicked!
    expect(createObjectURLMock).not.toHaveBeenCalled();
    expect(anchorClickMock).not.toHaveBeenCalled();
  });

  it('Probe R3: Hook refuses to publish detail response with mismatched opportunity_id, run_id, or query_key', async () => {
    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listA);

    // Detail returns item with opp-other and other-run and qk-other
    const corruptedDetail: OpportunityMonitorDetail = {
      ...detailA,
      query_key: 'qk-other',
      item: {
        ...detailA.item,
        opportunity_id: 'opp-other',
        run_id: 'other-run',
      },
    };

    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(corruptedDetail);

    const stableFilters: MonitorFilterState = { period: 'today', session: 'all', status: 'all' };
    let latestResult: any = null;
    function TestComponent() {
      latestResult = usePaperOpportunityMonitor({
        open: true,
        filters: stableFilters,
      });
      return <div>Hook Test</div>;
    }

    await act(async () => {
      root?.render(<TestComponent />);
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Select opp-1
    await act(async () => {
      latestResult.selectOpportunity('opp-1');
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Assertion: Corrupted detail must NOT be published, and detailError must be set
    expect(latestResult.detail).toBeNull();
    expect(latestResult.detailError).not.toBeNull();
  });

  it('Positive Control: Current valid focus chart request succeeds and triggers callback once', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listA);
    vi.spyOn(api, 'getOpportunityMonitorDetail').mockResolvedValue(detailA);

    vi.spyOn(api, 'getOpportunityMonitorChartTarget').mockResolvedValue({
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

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(handleFocusSetup).toHaveBeenCalledTimes(1);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('Positive Control: Current valid export downloads Markdown file with correct name', async () => {
    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listA);
    vi.spyOn(api, 'getOpportunityMonitorReportMarkdown').mockResolvedValue('# Valid Report Markdown');

    const createObjectURLMock = vi.fn().mockReturnValue('blob:valid-url');
    const revokeObjectURLMock = vi.fn();
    const anchorClickMock = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    globalThis.URL.createObjectURL = createObjectURLMock;
    globalThis.URL.revokeObjectURL = revokeObjectURLMock;

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={vi.fn()}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const downloadBtn = container?.querySelector('[data-testid="monitor-download-report-btn"]') as HTMLButtonElement;
    await act(async () => {
      downloadBtn.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    expect(anchorClickMock).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:valid-url');
  });

  it('Probe R2 Extension: Changing custom date inputs also cancels in-flight export', async () => {
    vi.spyOn(api, 'getOpportunityMonitorSummary').mockResolvedValue(summaryA);
    vi.spyOn(api, 'getOpportunityMonitorOpportunities').mockResolvedValue(listA);

    let resolveReport!: (val: string) => void;
    const reportPromise = new Promise<string>((resolve) => {
      resolveReport = resolve;
    });

    vi.spyOn(api, 'getOpportunityMonitorReportMarkdown').mockImplementation(() => reportPromise as any);

    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock-url');
    const anchorClickMock = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    globalThis.URL.createObjectURL = createObjectURLMock;

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={vi.fn()}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Switch to custom
    const customBtn = container?.querySelector('[data-testid="filter-period-custom"]') as HTMLButtonElement;
    await act(async () => {
      customBtn.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Start download
    const downloadBtn = container?.querySelector('[data-testid="monitor-download-report-btn"]') as HTMLButtonElement;
    await act(async () => {
      downloadBtn.click();
    });

    // Now change customStartDate input while export is in flight
    const startInput = container?.querySelector('input[type="date"]') as HTMLInputElement;
    expect(startInput).not.toBeNull();
    await act(async () => {
      const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      nativeSetter?.call(startInput, '2026-01-01');
      startInput.dispatchEvent(new Event('input', { bubbles: true }));
      startInput.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Release old report
    await act(async () => {
      resolveReport('# Old Report');
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(createObjectURLMock).not.toHaveBeenCalled();
    expect(anchorClickMock).not.toHaveBeenCalled();
  });
});

describe('Aurum V17.3 — Pure Lifecycle & Response Helpers', () => {
  const validBound = {
    symbol: 'XAUUSDT',
    scope: 'CG01',
    run_id: 'run-alpha',
    start_ms: 1000,
    end_ms: 2000,
    query_key: 'qk-1',
  };

  it('captureMonitorRequestLease: returns lease only for valid READY snapshot', () => {
    // Non-READY statuses return null
    expect(captureMonitorRequestLease({ status: 'CLOSED', epoch: 1, query: null })).toBeNull();
    expect(captureMonitorRequestLease({ status: 'LOADING', epoch: 2, query: null })).toBeNull();
    expect(captureMonitorRequestLease({ status: 'UNAVAILABLE', epoch: 3, query: null })).toBeNull();
    expect(captureMonitorRequestLease({ status: 'ERROR', epoch: 4, query: null })).toBeNull();

    // Invalid time window
    expect(
      captureMonitorRequestLease({
        status: 'READY',
        epoch: 1,
        query: { ...validBound, start_ms: 2000, end_ms: 1000 },
      })
    ).toBeNull();

    // Missing query_key
    expect(
      captureMonitorRequestLease({
        status: 'READY',
        epoch: 1,
        query: { ...validBound, query_key: '   ' },
      })
    ).toBeNull();

    // Valid READY returns lease
    const snapshot: MonitorContextSnapshot = {
      status: 'READY',
      epoch: 5,
      query: validBound,
    };
    const lease = captureMonitorRequestLease(snapshot);
    expect(lease).toEqual({
      epoch: 5,
      query: validBound,
    });
  });

  it('isMonitorRequestCurrent: rejects on epoch mismatch or status transition', () => {
    const lease: MonitorRequestLease = {
      epoch: 1,
      query: validBound,
    };

    // Current matches
    expect(isMonitorRequestCurrent({ status: 'READY', epoch: 1, query: validBound }, lease)).toBe(true);

    // Epoch mismatch (e.g. after refresh)
    expect(isMonitorRequestCurrent({ status: 'READY', epoch: 2, query: validBound }, lease)).toBe(false);

    // Status not READY (e.g. during LOADING or ERROR)
    expect(isMonitorRequestCurrent({ status: 'LOADING', epoch: 1, query: null }, lease)).toBe(false);
    expect(isMonitorRequestCurrent({ status: 'CLOSED', epoch: 1, query: null }, lease)).toBe(false);

    // Query key changed
    expect(
      isMonitorRequestCurrent(
        { status: 'READY', epoch: 1, query: { ...validBound, query_key: 'qk-other' } },
        lease
      )
    ).toBe(false);
  });

  it('assertMonitorDetailIdentity: verifies opportunity_id and run_id', () => {
    const goodDetail = {
      item: {
        opportunity_id: 'opp-1',
        run_id: 'run-alpha',
      },
    };

    // Valid
    expect(() => assertMonitorDetailIdentity(goodDetail, validBound, 'opp-1')).not.toThrow();

    // Malformed
    expect(() => assertMonitorDetailIdentity(null, validBound, 'opp-1')).toThrow(/MALFORMED_DETAIL/);
    expect(() => assertMonitorDetailIdentity({}, validBound, 'opp-1')).toThrow(/MALFORMED_DETAIL/);

    // Opportunity ID mismatch
    expect(() => assertMonitorDetailIdentity(goodDetail, validBound, 'opp-2')).toThrow(/ID_MISMATCH/);

    // Run ID mismatch
    const badRunDetail = {
      item: {
        opportunity_id: 'opp-1',
        run_id: 'run-beta',
      },
    };
    expect(() => assertMonitorDetailIdentity(badRunDetail, validBound, 'opp-1')).toThrow(/RUN_MISMATCH/);
  });

  it('assertMonitorResponseContext: strictly requires non-empty query_key matching bound', () => {
    // Missing query_key
    expect(() =>
      assertMonitorResponseContext({ query_key: '' }, validBound, 'DETAIL')
    ).toThrow(/SCOPE_MISMATCH/);

    expect(() =>
      assertMonitorResponseContext({}, validBound, 'DETAIL')
    ).toThrow(/SCOPE_MISMATCH/);

    // Mismatched query_key
    expect(() =>
      assertMonitorResponseContext({ query_key: 'qk-other' }, validBound, 'DETAIL')
    ).toThrow(/SCOPE_MISMATCH/);

    // Correct query_key
    expect(() =>
      assertMonitorResponseContext({ query_key: 'qk-1' }, validBound, 'DETAIL')
    ).not.toThrow();
  });
});

