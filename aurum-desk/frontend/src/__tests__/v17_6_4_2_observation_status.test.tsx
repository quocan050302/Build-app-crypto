// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { ObservationDashboard } from '../components/ObservationDashboard';
import { api } from '../api/client';
import type { ObservationDashboardSnapshot } from '../types/observation';

let host: HTMLDivElement;
let root: Root;

const createMockSnapshot = (overrides?: Partial<ObservationDashboardSnapshot>): ObservationDashboardSnapshot => {
  return {
    schema_version: '17.6.4.2',
    symbol: 'XAUUSDT',
    scope: 'CG01',
    run_id: 'run-v17642-status',
    window: {
      start_ms: 1792790400000,
      end_ms: 1792876800000,
      now_ms: 1792876800000,
    },
    engine: {
      engine_instance_id: 'worker-v17642-01',
      symbol: 'XAUUSDT',
      run_id: 'run-v17642-status',
      state: 'RUNNING',
      is_stalled: false,
      stalled_reason: null,
      last_process_heartbeat_at_ms: 1792876800000 - 1000,
      last_heartbeat_age_sec: 1.0,
      last_evaluation_started_at_ms: 1792876800000 - 1000,
      last_evaluation_completed_at_ms: 1792876800000 - 950,
      last_successful_evaluation_at_ms: 1792876800000 - 950,
      last_evaluation_age_sec: 1.0,
      last_cycle_id: 'cycle-01',
      cycles_count: 50,
      failed_cycles_count: 0,
      cadence_sec: 15,
      now_ms: 1792876800000,
      process_liveness: 'ALIVE',
      evaluation_health: 'HEALTHY',
      evidence_origin: 'LIVE_FORWARD',
      evidence_persistence_status: 'COMMITTED',
    },
    feed: {
      symbol: 'XAUUSDT',
      transport_state: 'CONNECTED',
      feed_source: 'WS',
      freshness_state: 'CONNECTED_FRESH',
      coverage_quality: 'GOOD',
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
      underlying_reference_state: 'OPEN',
      strategy_session_state: 'ACTIVE',
    },
    coverage: {
      total_span_hours: 24.0,
      observed_healthy_hours: 24.0,
      known_downtime_hours: 0.0,
      coverage_pct: 100.0,
      coverage_state: 'FULL',
      evidence_type: 'LIVE_PAPER_FORWARD_OBSERVATION',
    },
    evidence_origin: 'LIVE_FORWARD',
    evidence_persistence_status: 'COMMITTED',
    funnel: {},
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
      current_equity_usdt: 1000.0,
      net_pnl_usd: 0.0,
      closed_trades: 0,
    },
    incidents: [],
    ...overrides,
  };
};

describe('V17.6.4.2 Frontend Observation Persistence Status UI Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    host.remove();
  });

  const renderDashboard = async (snapshot: ObservationDashboardSnapshot) => {
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snapshot);
    await act(async () => {
      root.render(<ObservationDashboard />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  it('OS01: Displays warning banner when snapshot evidence_persistence_status is UNVERIFIED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'UNVERIFIED',
    });
    snap.engine.evidence_persistence_status = 'UNVERIFIED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('CẢNH BÁO LƯU TRỮ CHỨNG CỨ (PERSISTENCE DEGRADED)');
  });

  it('OS02: Displays warning banner when engine evidence_persistence_status is UNVERIFIED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'COMMITTED',
    });
    snap.engine.evidence_persistence_status = 'UNVERIFIED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('CẢNH BÁO LƯU TRỮ CHỨNG CỨ (PERSISTENCE DEGRADED)');
  });

  it('OS03: Displays warning banner when snapshot evidence_persistence_status is DEGRADED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'DEGRADED',
    });
    snap.engine.evidence_persistence_status = 'COMMITTED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('CẢNH BÁO LƯU TRỮ CHỨNG CỨ (PERSISTENCE DEGRADED)');
  });

  it('OS04: Does NOT display warning banner when evidence_persistence_status is COMMITTED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'COMMITTED',
    });
    snap.engine.evidence_persistence_status = 'COMMITTED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).toBeNull();
  });

  it('OS05: Renders emerald text style when engine evidence_persistence_status is COMMITTED', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_persistence_status = 'COMMITTED';
    await renderDashboard(snap);

    const statusElem = host.querySelector('[data-testid="evidence-persistence-status"]');
    expect(statusElem).not.toBeNull();
    expect(statusElem?.textContent).toBe('COMMITTED');
    expect(statusElem?.className).toContain('text-emerald-400');
  });

  it('OS06: Renders amber text style when engine evidence_persistence_status is UNVERIFIED', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_persistence_status = 'UNVERIFIED';
    await renderDashboard(snap);

    const statusElem = host.querySelector('[data-testid="evidence-persistence-status"]');
    expect(statusElem).not.toBeNull();
    expect(statusElem?.textContent).toBe('UNVERIFIED');
    expect(statusElem?.className).toContain('text-amber-400');
  });

  it('OS07: Renders rose text style when engine evidence_persistence_status is DEGRADED', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_persistence_status = 'DEGRADED';
    await renderDashboard(snap);

    const statusElem = host.querySelector('[data-testid="evidence-persistence-status"]');
    expect(statusElem).not.toBeNull();
    expect(statusElem?.textContent).toBe('DEGRADED');
    expect(statusElem?.className).toContain('text-rose-400');
  });
});
