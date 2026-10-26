// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { useObservationDashboard } from '../hooks/useObservationDashboard';
import { api } from '../api/client';

let host: HTMLDivElement;
let root: Root;

const mockSnapshot = {
  schema_version: '17.6',
  symbol: 'XAUUSDT',
  scope: 'CG01',
  run_id: 'run-hook-test',
  window: { start_ms: 1000, end_ms: 2000, now_ms: 2000 },
  engine: { state: 'RUNNING', cycles_count: 10, cadence_sec: 5 },
  feed: { transport_state: 'CONNECTED', freshness_state: 'CONNECTED_FRESH' },
  coverage: { coverage_pct: 98.5, coverage_state: 'FULL' },
  funnel: { detected: 2, watched: 1, armed: 1, filled: 1 },
  decisions: [],
  waiting_setups: [],
  blocked_categories: [],
  financial: { closed_trades: 0, winrate_pct: null, net_pnl_usd: 0.0 },
  incidents: [],
};

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);

  vi.spyOn(api, 'getObservationSummary').mockResolvedValue(mockSnapshot as any);
  vi.spyOn(api, 'getObservationDecisions').mockResolvedValue({
    items: [],
    total: 0,
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

it('hook bootstraps and returns observation snapshot and timezone toggling', async () => {
  let hookState: any = null;

  function TestComponent() {
    hookState = useObservationDashboard({ symbol: 'XAUUSDT' });
    return <div>Loaded: {hookState.snapshot ? 'YES' : 'NO'}</div>;
  }

  await act(async () => {
    root.render(<TestComponent />);
  });

  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });

  expect(hookState).not.toBeNull();
  expect(hookState.timezone).toBe('VN');

  // Toggle timezone
  act(() => {
    hookState.setTimezone('NY');
  });
  expect(hookState.timezone).toBe('NY');

  // Change range
  act(() => {
    hookState.setTimeRange('7D');
  });
  expect(hookState.timeRange).toBe('7D');
});
