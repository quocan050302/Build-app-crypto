// @vitest-environment happy-dom
/**
 * frontend/src/__tests__/PaperOpportunityMonitorDrawer.test.tsx
 * Aurum V17.0 — Paper Opportunity & Non-Entry Reason Monitor Drawer Tests
 *
 * Verifies (Phần 161 - 170, D1 - D6):
 * 1. Closed state: renders null, 0 network calls, 0 DOM elements.
 * 2. Open state: renders accessible dialog (role="dialog", aria-modal="true", "Theo Dõi Cơ Hội Paper").
 * 3. Master list: displays summary counts, filter buttons, opportunity list items.
 * 4. Master-detail navigation: clicking an opportunity displays its timeline & details; back button restores list.
 * 5. Keyboard accessibility: Escape key invokes onClose.
 * 6. Close button: invokes onClose.
 * 7. Focus chart action: invokes onFocusSetupOnChart and closes drawer.
 */

import { act } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';

import { PaperOpportunityMonitorDrawer } from '../components/PaperOpportunityMonitorDrawer';
import { api } from '../api/client';
import type {
  OpportunityMonitorSummary,
  MonitorListPage,
  OpportunityMonitorDetail,
} from '../types/paperOpportunityMonitor';

// @ts-ignore
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const mockSummary: OpportunityMonitorSummary = {
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
    total_unique_opportunities: 2,
    zones_detected: 2,
    zones_watched: 2,
    zones_touched: 2,
    trigger_confirmed: 1,
    plans_valid: 1,
    guard_blocked: 1,
    armed: 1,
    filled: 0,
    expired: 0,
    invalidated: 0,
  },
  top_reasons: [
    {
      code: 'WAITING_M1_MSS',
      unique_opportunity_count: 1,
      category: 'WAITING_TRIGGER',
      message_vi: 'Chờ nến M1 xác nhận',
    },
  ],
  financial: {
    metric_scope: 'RUN_WINDOW',
    description_vi: 'Lệnh của phiên quan sát',
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
  latency: {
    sample_count: 10,
    median_ms: 12.5,
    p95_ms: 35.0,
    source: 'TELEMETRY_TRACES',
  },
  limitations: ['STAGE_UPSERT_COARSE'],
  query_key: 'qk-test',
};

const mockListPage: MonitorListPage = {
  items: [
    {
      key: 'run-alpha:opp-101',
      opportunity_id: 'opp-101',
      run_id: 'run-alpha',
      model_id: 'CG01_SWEEP_MSS_FVG',
      direction: 'LONG',
      session_id: 'NY',
      session_label_vi: 'Phiên Mỹ (NY)',
      first_recorded_at_ms: 1699990000000,
      last_observed_at_ms: 1699995000000,
      current_stage: 'ZONE_DETECTED',
      stage_label_vi: 'Phát hiện vùng',
      primary_reason_code: 'WAITING_M1_MSS',
      primary_reason_vi: 'Chờ nến M1 phá vỡ cấu trúc MSS',
      classification: null,
      data_quality: 'VERIFIED',
      observed_milestone_count: 1,
      score: 78.5,
      score_source: 'EVALUATION',
      zone_label: 'M5 FVG Demand',
      chart_target: {
        kind: 'SETUP',
        id: 'setup-101',
        setup_instance_id: 'inst-1',
        revision: 1,
        evidence_snapshot_id: 'snap-1',
        verified: true,
      },
      limitations: [],
    },
    {
      key: 'run-alpha:opp-102',
      opportunity_id: 'opp-102',
      run_id: 'run-alpha',
      model_id: 'CG01_SWEEP_MSS_FVG',
      direction: 'SHORT',
      session_id: 'NY',
      session_label_vi: 'Phiên Mỹ (NY)',
      first_recorded_at_ms: 1699992000000,
      last_observed_at_ms: 1699996000000,
      current_stage: 'GUARD_BLOCKED',
      stage_label_vi: 'Bị chặn bởi Guard',
      primary_reason_code: 'GUARD_NEWS_HIGH_IMPACT',
      primary_reason_vi: 'Chặn do tin tức mạnh (CPI/NFP)',
      classification: 'POLICY_BLOCKED',
      data_quality: 'VERIFIED',
      observed_milestone_count: 2,
      score: 82.0,
      score_source: 'EVALUATION',
      zone_label: 'M15 Supply OB',
      chart_target: null,
      limitations: [],
    },
  ],
  total: 2,
  has_more: false,
  next_cursor: null,
  snapshot_as_of_ms: 1700000000000,
  limitations: [],
  query_key: 'qk-test',
  run_id: 'run-alpha',
};

const mockDetail: OpportunityMonitorDetail = {
  item: mockListPage.items[0],
  milestones: [
    {
      id: 'ms-1',
      stage: 'ZONE_DETECTED',
      stage_label_vi: 'Phát hiện vùng',
      first_recorded_at_ms: 1699990000000,
      last_updated_at_ms: 1699990000000,
      snapshot_as_of_ms: 1699990000000,
      occurred_at_ms: null,
      reason_codes: ['ZONE_QUALIFIED'],
      reason_summary_vi: 'Đã xác nhận vùng hợp lệ',
      source: 'FUNNEL_STAGE_SNAPSHOT',
      time_semantics: 'RECORDED',
      evidence_ids: ['fvg-999'],
      limitation: null,
    },
  ],
  plan: {
    entry: 2650.5,
    stop_loss: 2645.0,
    take_profit: 2665.0,
    direction: 'LONG',
    price_source: 'EVALUATION_PLAN',
    as_of_ms: 1699990000000,
  },
  order_outcomes: [],
  latency: {
    sample_count: 5,
    detector_runtime_ms: 12.0,
    feed_delay_ms: null,
    source: 'TRACE',
  },
  classifier_result: null,
  truncated: false,
  limitations: ['STAGE_UPSERT_COARSE'],
  query_key: 'qk-test',
};

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    getOpportunityMonitorSummary: vi.fn().mockImplementation(async () => mockSummary),
    getOpportunityMonitorOpportunities: vi.fn().mockImplementation(async () => mockListPage),
    getOpportunityMonitorDetail: vi.fn().mockImplementation(async () => mockDetail),
    getOpportunityMonitorReportMarkdown: vi.fn().mockResolvedValue('# Mock Report'),
    getOpportunityMonitorChartTarget: vi.fn().mockImplementation(async () => ({
      status: 'AVAILABLE',
      kind: 'SETUP',
      query_key: 'qk-test',
      opportunity_id: 'opp-101',
      target_id: 'setup-101',
      dto: {
        id: 'setup-101',
        setup_id: 'setup-101',
        setup_instance_id: 'inst-1',
        symbol: 'XAUUSDT',
        direction: 'LONG',
        provisional_entry: 2650.5,
        provisional_sl: 2645.0,
        provisional_tp: 2665.0,
        planned_entry: 2650.5,
        stop_loss: 2645.0,
        take_profit: 2665.0,
        version: 1,
        resolved_display_plan: {
          entry: 2650.5,
          stop_loss: 2645.0,
          take_profit: 2665.0,
          direction: 'LONG',
        },
      },
    })),
    api: {
      getOpportunityMonitorSummary: vi.fn().mockImplementation(async () => mockSummary),
      getOpportunityMonitorOpportunities: vi.fn().mockImplementation(async () => mockListPage),
      getOpportunityMonitorDetail: vi.fn().mockImplementation(async () => mockDetail),
      getOpportunityMonitorReportMarkdown: vi.fn().mockResolvedValue('# Mock Report'),
      getOpportunityMonitorChartTarget: vi.fn().mockImplementation(async () => ({
        status: 'AVAILABLE',
        kind: 'SETUP',
        query_key: 'qk-test',
        opportunity_id: 'opp-101',
        target_id: 'setup-101',
        dto: {
          id: 'setup-101',
          setup_id: 'setup-101',
          setup_instance_id: 'inst-1',
          symbol: 'XAUUSDT',
          direction: 'LONG',
          provisional_entry: 2650.5,
          provisional_sl: 2645.0,
          provisional_tp: 2665.0,
          planned_entry: 2650.5,
          stop_loss: 2645.0,
          take_profit: 2665.0,
          version: 1,
          resolved_display_plan: {
            entry: 2650.5,
            stop_loss: 2645.0,
            take_profit: 2665.0,
            direction: 'LONG',
          },
        },
      })),
    },
  };
});

describe('Aurum V17.0 — PaperOpportunityMonitorDrawer Component Tests', () => {
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
    vi.clearAllMocks();
  });

  it('renders nothing when open is false', () => {
    act(() => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={false}
          onClose={vi.fn()}
        />
      );
    });

    const dialog = container?.querySelector('[role="dialog"]');
    expect(dialog).toBeNull();
  });

  it('renders accessible dialog when open is true', async () => {
    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={vi.fn()}
        />
      );
    });

    const dialog = container?.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute('aria-modal')).toBe('true');
    expect(dialog?.getAttribute('data-testid')).toBe('paper-opportunity-monitor-drawer');

    // Title exists
    const title = container?.querySelector('h2');
    expect(title?.textContent).toContain('Theo Dõi Cơ Hội Paper');
  });

  it('renders summary statistics and list items', async () => {
    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={vi.fn()}
        />
      );
    });

    // Wait for async state updates
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Verify summary counts exist
    expect(container?.textContent).toContain('Tổng cơ hội: 2');
    expect(container?.textContent).toContain('Chạm vùng');
    expect(container?.textContent).toContain('Trigger');
    expect(container?.textContent).toContain('Đã khớp');

    // Verify opportunity items
    const oppItems = container?.querySelectorAll('[data-testid^="monitor-row-"]');
    expect(oppItems?.length).toBe(2);

    expect(container?.textContent).toContain('Phát hiện vùng');
    expect(container?.textContent).toContain('Bị chặn bởi Guard');
  });

  it('switches to detail view when clicking an opportunity row and returns via back button', async () => {
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

    const row = container?.querySelector('[data-testid="monitor-row-opp-101"]') as HTMLElement;
    expect(row).not.toBeNull();

    // Click row to navigate to detail
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Detail header with back button should be visible
    const backBtn = container?.querySelector('button[title="Quay lại danh sách"]') as HTMLButtonElement;
    expect(backBtn).not.toBeNull();

    // Check detail content
    expect(container?.textContent).toContain('Mức Giá Kế Hoạch (Dự Kiến)');
    expect(container?.textContent).toContain('2650.50');

    // Click back button to return to master list
    await act(async () => {
      backBtn.click();
    });

    // Master list should be visible again
    const oppItems = container?.querySelectorAll('[data-testid^="monitor-row-"]');
    expect(oppItems?.length).toBe(2);
  });

  it('invokes onClose when clicking close button or pressing Escape', async () => {
    const handleClose = vi.fn();

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={handleClose}
        />
      );
    });

    // Click close button
    const closeBtn = container?.querySelector('button[data-testid="monitor-close-btn"]') as HTMLButtonElement;
    expect(closeBtn).not.toBeNull();

    await act(async () => {
      closeBtn.click();
    });

    expect(handleClose).toHaveBeenCalledTimes(1);

    // Press Escape key
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });

    expect(handleClose).toHaveBeenCalledTimes(2);
  });

  it('invokes onFocusSetupOnChart and closes drawer when clicking Xem trên chart', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

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

    // Navigate to opp-101 detail
    const row = container?.querySelector('[data-testid="monitor-row-opp-101"]') as HTMLElement;
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Find and click focus chart button
    const focusBtn = container?.querySelector('[data-testid="monitor-focus-chart-btn"]') as HTMLButtonElement;
    expect(focusBtn).not.toBeNull();

    await act(async () => {
      focusBtn.click();
    });

    // Must close drawer first then trigger chart focus
    expect(handleClose).toHaveBeenCalledTimes(1);
    expect(handleFocusSetup).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'setup-101',
        setup_id: 'setup-101',
        setup_instance_id: 'inst-1',
        symbol: 'XAUUSDT',
        direction: 'LONG',
        planned_entry: 2650.5,
        stop_loss: 2645.0,
        take_profit: 2665.0,
      })
    );
  });

  it('Failure M3 Regression: invokes onFocusTradeOnChart with full canonical ORDER DTO (planned_entry, stop_loss, cost_snapshot)', async () => {
    const handleClose = vi.fn();
    const handleFocusTrade = vi.fn();

    // Mock detail to have an ORDER chart_target
    const orderDetail: OpportunityMonitorDetail = {
      ...mockDetail,
      item: {
        ...mockDetail.item,
        chart_target: {
          kind: 'ORDER',
          id: 'ord-101',
          setup_instance_id: 'inst-1',
          revision: 1,
          evidence_snapshot_id: 'snap-1',
          verified: true,
        },
      },
    };

    vi.mocked(api.getOpportunityMonitorDetail).mockImplementationOnce(async () => orderDetail);
    vi.mocked(api.getOpportunityMonitorChartTarget).mockImplementationOnce(async () => ({
      status: 'AVAILABLE',
      kind: 'ORDER',
      query_key: 'qk-test',
      opportunity_id: 'opp-101',
      target_id: 'ord-101',
      dto: {
        id: 'ord-101',
        order_id: 'ord-101',
        setup_id: 'setup-101',
        instrument: 'XAUUSDT',
        direction: 'LONG',
        state: 'FILLED',
        order_type: 'LIMIT',
        timeframe: 'M5',
        planned_entry: 2650.5,
        actual_entry: 2650.5,
        stop_loss: 2645.0,
        take_profit: 2665.0,
        quantity: 0.1,
        cost_snapshot: {
          minimum_snapshot_rr: 2.0,
        },
      },
    }));

    await act(async () => {
      root?.render(
        <PaperOpportunityMonitorDrawer
          open={true}
          onClose={handleClose}
          onFocusTradeOnChart={handleFocusTrade}
        />
      );
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const row = container?.querySelector('[data-testid="monitor-row-opp-101"]') as HTMLElement;
    await act(async () => {
      row.click();
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const focusBtn = container?.querySelector('[data-testid="monitor-focus-chart-btn"]') as HTMLButtonElement;
    expect(focusBtn).not.toBeNull();

    await act(async () => {
      focusBtn.click();
    });

    // Drawer closes and full canonical ORDER DTO is passed
    expect(handleClose).toHaveBeenCalledTimes(1);
    expect(handleFocusTrade).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'ord-101',
        order_id: 'ord-101',
        instrument: 'XAUUSDT',
        direction: 'LONG',
        state: 'FILLED',
        planned_entry: 2650.5,
        stop_loss: 2645.0,
        take_profit: 2665.0,
        cost_snapshot: {
          minimum_snapshot_rr: 2.0,
        },
      })
    );
  });

  it('handles stale or unavailable target by displaying inline error and keeping drawer open', async () => {
    const handleClose = vi.fn();
    const handleFocusSetup = vi.fn();

    vi.mocked(api.getOpportunityMonitorChartTarget).mockImplementationOnce(async () => ({
      status: 'STALE_TARGET',
      query_key: 'qk-test',
      opportunity_id: 'opp-101',
      reason_code: 'SETUP_REVISION_STALE',
      message_vi: 'Kế hoạch đã thay đổi phiên bản mới',
    }));

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

    const row = container?.querySelector('[data-testid="monitor-row-opp-101"]') as HTMLElement;
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

    // Drawer should NOT close and handlers should NOT be invoked
    expect(handleClose).not.toHaveBeenCalled();
    expect(handleFocusSetup).not.toHaveBeenCalled();

    // Inline error displayed
    const errorEl = container?.querySelector('[data-testid="monitor-focus-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain('Kế hoạch đã thay đổi');
  });
});
