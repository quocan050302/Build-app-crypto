import type { ChartBinding, Direction } from '../types/smcScenario';

export function createSetupSelection(setup: any, planFingerprint?: string | null): ChartBinding {
  return {
    source: 'SETUP',
    id: setup.id,
    instanceId: setup.setup_instance_id || null,
    revision: setup.version ?? 1,
    symbol: setup.symbol || 'XAUUSDT',
    direction: (setup.direction as Direction) || null,
    planFingerprint: planFingerprint || null,
    snapshotId: setup.scenario_snapshot_id || null,
  };
}

export function createOrderSelection(order: any): ChartBinding {
  return {
    source: 'ORDER',
    id: order.id,
    instanceId: order.setup_instance_id || null,
    revision: 1,
    symbol: order.instrument || 'XAUUSDT',
    direction: (order.direction as Direction) || null,
    planFingerprint: null,
    snapshotId: order.scenario_snapshot_id || null,
  };
}

export function createScenarioSelection(scenario: any): ChartBinding {
  return {
    source: 'SCENARIO',
    id: scenario.scenario_id,
    instanceId: scenario.instance_id || null,
    revision: scenario.revision ?? 1,
    symbol: scenario.symbol || 'XAUUSDT',
    direction: (scenario.direction as Direction) || null,
    planFingerprint: scenario.payload?.plan?.plan_fingerprint || null,
    snapshotId: scenario.current_snapshot_id || null,
  };
}
