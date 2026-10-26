// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { ObservationDashboard } from '../components/ObservationDashboard';
import { api } from '../api/client';
import type { ObservationDashboardSnapshot } from '../types/observation';

let host: HTMLDivElement;
let root: Root;

const baseSnapshot: ObservationDashboardSnapshot = {
  schema_version: '17.6.4',
  symbol: 'XAUUSDT',
  scope: 'CG01',
  run_id: 'run-v1764-truth',
  window: {
    start_ms: 1792790400000,
    end_ms: 1792876800000,
    now_ms: 1792876800000,
  },
  engine: {
    engine_instance_id: 'worker-v1764-01',
    symbol: 'XAUUSDT',
    run_id: 'run-v1764-truth',
    state: 'RUNNING',
    is_stalled: false,
    stalled_reason: null,
    last_process_heartbeat_at_ms: 1792876800000 - 1000,
    last_heartbeat_age_sec: 1.0,
    last_evaluation_started_at_ms: 1792876800000 - 1000,
    last_evaluation_completed_at_ms: 1792876800000 - 950,
    last_successful_evaluation_at_ms: 1792876800000 - 950,
    last_evaluation_age_sec: 1.0,
    last_cycle_id: 'cycle-truth-01',
    cycles_count: 100,
    failed_cycles_count: 0,
    cadence_sec: 15,
    now_ms: 1792876800000,
  },
  feed: {
    symbol: 'XAUUSDT',
    transport_state: 'CONNECTED',
    feed_source: 'WS',
    freshness_state: 'CONNECTED_FRESH',
    is_fresh: true,
    is_weekend_break: false,
    reconnect_count: 0,
    last_success_time: 1792876800000,
    last_quote_at_ms: 1792876800000 - 400,
    quote_age_sec: 0.4,
    bid: 2735.10,
    ask: 2735.30,
    mid: 2735.20,
    timeframe_health: {},
    has_gap: false,
    now_ms: 1792876800000,
    contract_schedule: '24_7',
    contract_operational_state: 'OPEN',
    underlying_reference_state: 'CLOSED_WEEKEND_BREAK',
    strategy_session_state: 'DISABLED_OUTSIDE_WINDOW',
  },
  coverage: {
    total_span_hours: 24.0,
    observed_healthy_hours: 24.0,
    known_downtime_hours: 0.0,
    coverage_pct: 100.0,
    coverage_state: 'FULL',
    evidence_type: 'LIVE_PAPER_FORWARD_OBSERVATION',
    coverage_policy_version: 'V17.6.4_BITGET_PERPETUAL_24_7',
    schedule_source: 'BITGET_PRODUCT_SPEC',
  },
  funnel: {
    zones_detected: 12,
    zones_touched: 9,
    entry_confirmed: 6,
    blocked: 3,
    armed: 2,
    filled: 1,
    conversion_rates: {},
    top_blocked_reasons: [],
    median_decision_latency_ms: 14.5,
    p95_decision_latency_ms: 22.0,
  },
  decisions: {
    items: [],
    limit: 20,
    has_more: false,
  },
  waiting_setups: [],
  blocked_categories: [],
  financial: {
    status: 'ACTIVE',
    initial_capital_usdt: 1000.0,
    current_equity_usdt: 1050.0,
    net_pnl_usd: 50.0,
    closed_trades: 2,
    wins: 2,
    losses: 0,
    breakevens: 0,
    winrate_pct: 100.0,
    max_drawdown_usd: 5.0,
    max_drawdown_pct: 0.5,
  },
  incidents: [],
};

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  vi.restoreAllMocks();
});

describe('V17.6.4 Observation Dashboard Truthfulness Tests (UI01 - UI12)', () => {
  it('UI01: renders NOT_EVALUATED when snapshot has no latest_cycle', async () => {
    const snap = { ...baseSnapshot, latest_cycle: null };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('NOT_EVALUATED');
  });

  it('UI02: renders Opportunities Detected as N/A when latest_cycle is absent', async () => {
    const snap = { ...baseSnapshot, latest_cycle: null };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Cơ hội phát hiện:N/A');
  });

  it('UI03: renders Market Regime as UNKNOWN when latest_cycle is absent', async () => {
    const snap = { ...baseSnapshot, latest_cycle: null };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Market Regime:UNKNOWN');
  });

  it('UI04: renders cycle ID and decision status when latest_cycle is present', async () => {
    const snap = {
      ...baseSnapshot,
      latest_cycle: {
        cycle_id: 'cycle-verified-777',
        run_id: 'run-v1764-truth',
        symbol: 'XAUUSDT',
        started_at_ms: 1792876800000 - 200,
        completed_at_ms: 1792876800000 - 50,
        duration_ms: 150,
        decision_status: 'ARMED',
        opportunities_detected: 3,
        market_regime: 'TRENDING_BULL',
      },
    };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('ARMED');
    expect(host.textContent).toContain('cycle-verified-777');
    expect(host.textContent).toContain('Cơ hội phát hiện:3');
    expect(host.textContent).toContain('Market Regime:TRENDING_BULL');
  });

  it('UI05: renders Run not found and N/A when financial status is NOT_FOUND', async () => {
    const snap = {
      ...baseSnapshot,
      financial: {
        status: 'NOT_FOUND',
        currency: null,
        initial_capital_usdt: null,
        current_equity_usdt: null,
        net_pnl_usd: null,
        net_roi_pct: null,
        total_trades: null,
        open_trades: null,
        closed_trades: null,
        winning_trades: null,
        losing_trades: null,
        break_even_trades: null,
        win_rate_pct: null,
        max_drawdown_usd: null,
        max_drawdown_pct: null,
        profit_factor: null,
        fees_paid_usd: null,
      },
    };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Run not found');
    expect(host.textContent).toContain('Vốn Hiện Tại (Equity):N/A');
    expect(host.textContent).toContain('Lợi Nhuận Ròng (Net PnL):N/A');
  });

  it('UI06: renders N/A fail-closed when financial object is null', async () => {
    const snap = { ...baseSnapshot, financial: null };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Vốn Khởi Điểm: N/A');
    expect(host.textContent).toContain('Vốn Hiện Tại (Equity):N/A');
    expect(host.textContent).toContain('Lệnh Đã Đóng:N/A');
  });

  it('UI07: renders 24/7 Bitget contract schedule in feed card', async () => {
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(baseSnapshot as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('24/7 (Bitget)');
  });

  it('UI08: renders contract operational state when available in feed', async () => {
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(baseSnapshot as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Contract: OPEN');
  });

  it('UI09: renders underlying reference session and strategy window states', async () => {
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(baseSnapshot as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('Ref Market: CLOSED_WEEKEND_BREAK');
    expect(host.textContent).toContain('Strategy: DISABLED_OUTSIDE_WINDOW');
  });

  it('UI10: renders RUN_NOT_FOUND coverage state accurately', async () => {
    const snap = {
      ...baseSnapshot,
      coverage: {
        ...baseSnapshot.coverage,
        coverage_pct: null,
        coverage_state: 'RUN_NOT_FOUND',
      },
    };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('RUN_NOT_FOUND');
    expect(host.textContent).toContain('N/A');
  });

  it('UI11: renders NO_ELIGIBLE_WINDOW coverage state accurately', async () => {
    const snap = {
      ...baseSnapshot,
      coverage: {
        ...baseSnapshot.coverage,
        coverage_pct: null,
        coverage_state: 'NO_ELIGIBLE_WINDOW',
      },
    };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('NO_ELIGIBLE_WINDOW');
    expect(host.textContent).toContain('N/A');
  });

  it('UI12: renders winrate N/A (0 lệnh) when closed_trades is 0', async () => {
    const snap = {
      ...baseSnapshot,
      financial: {
        ...baseSnapshot.financial!,
        closed_trades: 0,
        winning_trades: 0,
        losing_trades: 0,
        win_rate_pct: 0.0,
      },
    };
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap as any);

    await act(async () => {
      root.render(<ObservationDashboard />);
    });

    expect(host.textContent).toContain('N/A (0 lệnh)');
  });
});
