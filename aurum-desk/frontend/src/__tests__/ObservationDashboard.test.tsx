// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { ObservationDashboard } from '../components/ObservationDashboard';
import { api } from '../api/client';

let host: HTMLDivElement;
let root: Root;

const mockSnapshot = {
  schema_version: '17.6',
  symbol: 'XAUUSDT',
  scope: 'CG01',
  run_id: 'run-2026-test',
  window: {
    start_ms: 1700000000000,
    end_ms: 1700000060000,
    now_ms: 1700000060000,
  },
  engine: {
    engine_instance_id: 'eng-ui-01',
    symbol: 'XAUUSDT',
    run_id: 'run-2026-test',
    state: 'RUNNING',
    is_stalled: false,
    stalled_reason: null,
    last_process_heartbeat_at_ms: 1700000059000,
    last_heartbeat_age_sec: 1.0,
    last_evaluation_started_at_ms: 1700000058000,
    last_evaluation_completed_at_ms: 1700000059000,
    last_successful_evaluation_at_ms: 1700000059000,
    last_evaluation_age_sec: 1.0,
    last_cycle_id: 'cycle-01',
    cycles_count: 42,
    failed_cycles_count: 0,
    cadence_sec: 5,
    now_ms: 1700000060000,
  },
  feed: {
    symbol: 'XAUUSDT',
    transport_state: 'CONNECTED',
    feed_source: 'WS',
    freshness_state: 'CONNECTED_FRESH',
    is_fresh: true,
    last_quote_at_ms: 1700000059500,
    quote_age_sec: 0.5,
    bid: 2725.10,
    ask: 2725.30,
    mid: 2725.20,
    candles_freshness: {
      '1M': { closed_at_ms: 1700000000000, age_sec: 60.0, is_fresh: true },
      '5M': { closed_at_ms: 1700000000000, age_sec: 60.0, is_fresh: true },
    },
    data_gaps_detected: false,
    is_market_break: false,
    now_ms: 1700000060000,
  },
  coverage: {
    total_span_hours: 1.0,
    observed_healthy_hours: 1.0,
    known_downtime_hours: 0.0,
    coverage_pct: 100.0,
    coverage_state: 'FULL',
    evidence_type: 'LIVE_PAPER_FORWARD_OBSERVATION',
  },
  funnel: {
    detected: 5,
    watched: 3,
    touched: 2,
    triggered: 1,
    qualified: 1,
    blocked: 1,
    armed: 1,
    filled: 1,
  },
  decisions: {
    items: [
      {
        event_id: 'ev-test-01',
        opportunity_id: 'opp-101',
        model_id: 'STANDARD_SMC',
        direction: 'SHORT',
        stage: 'ARMED',
        stage_title_vi: 'Đã sẵn sàng (Armed)',
        primary_reason_code: 'ALL_CONDITIONS_MET',
        reason_category: 'STRUCTURE',
        reason_category_title_vi: 'Cấu trúc thị trường',
        short_message_vi: 'Đủ điều kiện vào lệnh',
        technical_message: 'All SMC/ICT conditions verified',
        occurred_at_ms: 1700000055000,
      },
    ],
    limit: 20,
    has_more: false,
    next_cursor: null,
  },
  waiting_setups: [
    {
      opportunity_id: 'opp-wait-01',
      direction: 'LONG',
      stage: 'WAITING',
      stage_title_vi: 'Đang chờ (Waiting)',
      conditions: [
        {
          id: 'LIQUIDITY_SWEEP',
          name_vi: 'Quét thanh khoản (Liquidity Sweep)',
          status: 'FULFILLED',
          detail: 'Đã quét đỉnh/đáy',
        },
        {
          id: 'MSS_CONFIRMATION',
          name_vi: 'Phá vỡ cấu trúc (MSS)',
          status: 'WAITING',
          detail: 'Chờ nến M1 đóng xác nhận MSS',
        },
      ],
    },
  ],
  blocked_categories: [
    {
      category: 'NET_RR',
      category_title_vi: 'Tỷ lệ Net R:R',
      count: 2,
    },
  ],
  financial: {
    closed_trades: 0,
    open_trades: 0,
    armed_orders: 1,
    wins: 0,
    losses: 0,
    breakevens: 0,
    winrate_pct: null,
    net_pnl_usd: 0.0,
    net_expectancy: 0.0,
    max_drawdown_usd: 0.0,
    max_drawdown_pct: 0.0,
    initial_capital_usdt: 1000.0,
    current_equity_usdt: 1000.0,
  },
  incidents: [],
  latest_cycle: {
    cycle_id: 'cycle-01',
    status: 'COMPLETED',
    decision_status: 'ARMED',
    duration_ms: 45.2,
    opportunities_detected: 1,
  },
};

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);

  vi.spyOn(api, 'getObservationSummary').mockResolvedValue(mockSnapshot as any);
  vi.spyOn(api, 'getObservationDecisions').mockResolvedValue({
    items: mockSnapshot.decisions,
    total: 1,
    has_more: false,
    next_cursor: null,
  } as any);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
  vi.restoreAllMocks();
});

it('renders observation executive status cards and live metrics', async () => {
  await act(async () => {
    root.render(<ObservationDashboard />);
  });

  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });

  // Verify header & sections
  expect(host.textContent).toContain('Quan Sát & Trí Tuệ Quyết Định Bot');
  expect(host.textContent).toContain('1. Bot Engine Health');
  expect(host.textContent).toContain('2. Báo Giá & Dữ Liệu');
  expect(host.textContent).toContain('3. Đánh Giá SMC/ICT');
  expect(host.textContent).toContain('5. Độ Bao Phủ Quan Sát');

  // Verify engine & feed values
  expect(host.textContent).toContain('RUNNING');
  expect(host.textContent).toContain('TRỰC TIẾP & MỚI (LIVE)');
  expect(host.textContent).toContain('2725.10');
  expect(host.textContent).toContain('2725.30');

  // Verify fail-closed winrate (N/A when 0 closed trades)
  expect(host.textContent).toContain('N/A (0 lệnh)');

  // Verify waiting conditions panel
  expect(host.textContent).toContain('Điều Kiện SMC Đang Chờ');
  expect(host.textContent).toContain('Quét thanh khoản (Liquidity Sweep)');
  expect(host.textContent).toContain('Phá vỡ cấu trúc (MSS)');

  // Verify recent decisions table
  expect(host.textContent).toContain('Dòng Thời Gian Quyết Định Gần Nhất');
  expect(host.textContent).toContain('Đã sẵn sàng (Armed)');
  expect(host.textContent).toContain('Đủ điều kiện vào lệnh');
});
