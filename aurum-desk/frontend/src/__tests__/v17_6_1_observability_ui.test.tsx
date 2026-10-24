// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { ObservationDashboard } from '../components/ObservationDashboard';
import { api } from '../api/client';

let host: HTMLDivElement;
let root: Root;

const mockSnapshotV1761 = {
  schema_version: '17.6.1',
  symbol: 'XAUUSDT',
  scope: 'CG01',
  run_id: 'run-2026-v1761',
  window: {
    start_ms: 1792591200000 - 86400000,
    end_ms: 1792591200000,
    now_ms: 1792591200000,
  },
  engine: {
    engine_instance_id: 'worker-v1761-01',
    symbol: 'XAUUSDT',
    run_id: 'run-2026-v1761',
    state: 'RUNNING',
    is_stalled: false,
    stalled_reason: null,
    last_process_heartbeat_at_ms: 1792591200000 - 1000,
    last_heartbeat_age_sec: 1.0,
    last_evaluation_started_at_ms: 1792591200000 - 1000,
    last_evaluation_completed_at_ms: 1792591200000 - 980,
    last_successful_evaluation_at_ms: 1792591200000 - 980,
    last_evaluation_age_sec: 1.0,
    last_cycle_id: 'cycle-v1761',
    cycles_count: 50,
    failed_cycles_count: 0,
    cadence_sec: 15,
    now_ms: 1792591200000,
  },
  feed: {
    symbol: 'XAUUSDT',
    transport_state: 'CONNECTED',
    feed_source: 'WS',
    freshness_state: 'CONNECTED_FRESH',
    is_fresh: true,
    last_quote_at_ms: 1792591200000 - 500,
    quote_age_sec: 0.5,
    bid: 2730.10,
    ask: 2730.30,
    mid: 2730.20,
    timeframe_health: {},
    has_gap: false,
    now_ms: 1792591200000,
  },
  coverage: {
    total_span_hours: 24.0,
    market_open_hours: 23.0,
    observed_healthy_hours: 23.0,
    known_downtime_hours: 0.0,
    coverage_pct: 100.0,
    coverage_state: 'FULL',
    evidence_type: 'LIVE_PAPER_FORWARD_OBSERVATION',
  },
  funnel: {
    zones_detected: 10,
    zones_touched: 8,
    entry_confirmed: 5,
    blocked: 3,
    armed: 2,
    filled: 1,
    conversion_rates: {},
    top_blocked_reasons: [],
    median_decision_latency_ms: 15.0,
    p95_decision_latency_ms: 25.0,
  },
  decisions: [],
  waiting_setups: [],
  blocked_categories: [],
  // Intentionally null financial to test fail-closed presentation
  financial: null,
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

it('renders V17.6.1 header badge and fail-closed financial presentation when financial is null', async () => {
  vi.spyOn(api, 'getObservationSummary').mockResolvedValue(mockSnapshotV1761 as any);

  await act(async () => {
    root.render(<ObservationDashboard />);
  });

  // Verify V17.6.1 version tag
  expect(host.textContent).toContain('V17.6.1 OBSERVABILITY & DECISION TELEMETRY');

  // Verify fail-closed financial rendering (N/A instead of fake $1,000.00 or $0.00)
  expect(host.textContent).toContain('Vốn Khởi Điểm: N/A');
  expect(host.textContent).toContain('Lệnh Đã Đóng:N/A');
  expect(host.textContent).toContain('Vốn Hiện Tại (Equity):N/A');
  expect(host.textContent).toContain('Thắng / Thua / Hòa:N/A');
});

it('renders winrate N/A (0 lệnh) when closed_trades is 0', async () => {
  const snapshotWithZeroTrades = {
    ...mockSnapshotV1761,
    financial: {
      initial_capital_usdt: 1000.0,
      current_equity_usdt: 1000.0,
      net_pnl_usd: 0.0,
      closed_trades: 0,
      open_trades: 0,
      armed_orders: 0,
      wins: 0,
      losses: 0,
      breakevens: 0,
      winrate_pct: null,
      max_drawdown_usd: 0.0,
      max_drawdown_pct: 0.0,
    },
  };

  vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snapshotWithZeroTrades as any);

  await act(async () => {
    root.render(<ObservationDashboard />);
  });

  expect(host.textContent).toContain('N/A (0 lệnh)');
  expect(host.textContent).toContain('Vốn Khởi Điểm: $1000.00 USDT');
});
