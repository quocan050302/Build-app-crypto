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
    schema_version: '17.6.4.1',
    symbol: 'XAUUSDT',
    scope: 'CG01',
    run_id: 'run-v17641-evidence',
    window: {
      start_ms: 1792790400000,
      end_ms: 1792876800000,
      now_ms: 1792876800000,
    },
    engine: {
      engine_instance_id: 'worker-v17641-01',
      symbol: 'XAUUSDT',
      run_id: 'run-v17641-evidence',
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

describe('V17.6.4.1 Frontend Observation Evidence & Persistence UI Tests', () => {
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
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snapshot as any);
    await act(async () => {
      root.render(<ObservationDashboard symbol="XAUUSDT" scope="CG01" runId="run-v17641-evidence" />);
    });
    // Give promises time to resolve and state to update
    await act(async () => {
      await Promise.resolve();
    });
  };

  it('UI01: Displays UNVERIFIED badge when feed evidence quality is UNVERIFIED', async () => {
    const snap = createMockSnapshot();
    snap.feed.coverage_quality = 'UNVERIFIED';
    await renderDashboard(snap);

    expect(host.textContent).toContain('CHƯA XÁC MINH (UNVERIFIED)');
  });

  it('UI02: Displays GOOD badge when feed evidence quality is GOOD', async () => {
    const snap = createMockSnapshot();
    snap.feed.coverage_quality = 'GOOD';
    await renderDashboard(snap);

    expect(host.textContent).toContain('CHỨNG CỨ TỐT (GOOD)');
  });

  it('UI03: Displays DEGRADED badge when feed evidence quality is DEGRADED', async () => {
    const snap = createMockSnapshot();
    snap.feed.coverage_quality = 'DEGRADED';
    await renderDashboard(snap);

    expect(host.textContent).toContain('SUY GIẢM (DEGRADED)');
  });

  it('UI04: Displays warning banner when evidence persistence status is DEGRADED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'DEGRADED',
    });
    snap.engine.evidence_persistence_status = 'DEGRADED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain('CẢNH BÁO LƯU TRỮ CHỨNG CỨ (PERSISTENCE DEGRADED)');
  });

  it('UI05: Does NOT display persistence warning banner when persistence status is COMMITTED', async () => {
    const snap = createMockSnapshot({
      evidence_persistence_status: 'COMMITTED',
    });
    snap.engine.evidence_persistence_status = 'COMMITTED';
    await renderDashboard(snap);

    const banner = host.querySelector('[data-testid="persistence-degraded-banner"]');
    expect(banner).toBeNull();
  });

  it('UI06: Displays process liveness distinct from evaluation health', async () => {
    const snap = createMockSnapshot();
    snap.engine.process_liveness = 'ALIVE';
    snap.engine.evaluation_health = 'HEALTHY';
    await renderDashboard(snap);

    expect(host.textContent).toContain('Tiến trình (Process):');
    expect(host.textContent).toContain('ALIVE');
    expect(host.textContent).toContain('Đánh giá (Health):');
    expect(host.textContent).toContain('HEALTHY');
  });

  it('UI07: Displays evidence origin LIVE_FORWARD', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_origin = 'LIVE_FORWARD';
    await renderDashboard(snap);

    expect(host.textContent).toContain('Nguồn chứng cứ:');
    expect(host.textContent).toContain('LIVE_FORWARD');
  });

  it('UI08: Displays evidence origin REPLAY when replay isolated', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_origin = 'REPLAY';
    await renderDashboard(snap);

    expect(host.textContent).toContain('Nguồn chứng cứ:');
    expect(host.textContent).toContain('REPLAY');
  });

  it('UI09: Displays evidence persistence status in engine card', async () => {
    const snap = createMockSnapshot();
    snap.engine.evidence_persistence_status = 'COMMITTED';
    await renderDashboard(snap);

    expect(host.textContent).toContain('Lưu trữ chứng cứ:');
    expect(host.textContent).toContain('COMMITTED');
  });

  it('UI10: Handles missing optional evidence fields gracefully without crashing', async () => {
    const snap = createMockSnapshot();
    delete snap.evidence_origin;
    delete snap.evidence_persistence_status;
    delete (snap.engine as any).process_liveness;
    delete (snap.engine as any).evaluation_health;
    delete (snap.engine as any).evidence_origin;
    delete (snap.engine as any).evidence_persistence_status;
    delete (snap.feed as any).coverage_quality;

    await renderDashboard(snap);
    expect(host.textContent).toContain('Bot Engine Health');
  });

  it('UI11: Renders distinct state for STALLED process liveness', async () => {
    const snap = createMockSnapshot();
    snap.engine.process_liveness = 'STALLED';
    snap.engine.evaluation_health = 'STALLED';
    await renderDashboard(snap);

    expect(host.textContent).toContain('STALLED');
  });

  it('UI12: Updates correctly when snapshot refreshes with new evidence quality', async () => {
    const snap1 = createMockSnapshot();
    snap1.feed.coverage_quality = 'GOOD';
    await renderDashboard(snap1);
    expect(host.textContent).toContain('CHỨNG CỨ TỐT (GOOD)');

    const snap2 = createMockSnapshot();
    snap2.feed.coverage_quality = 'UNVERIFIED';
    vi.spyOn(api, 'getObservationSummary').mockResolvedValue(snap2 as any);

    const refreshBtn = host.querySelector('button[title*="Tải lại"]') as HTMLButtonElement;
    await act(async () => {
      refreshBtn?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(host.textContent).toContain('CHƯA XÁC MINH (UNVERIFIED)');
  });
});
