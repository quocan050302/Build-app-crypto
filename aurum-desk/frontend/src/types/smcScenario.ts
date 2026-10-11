export type Direction = 'LONG' | 'SHORT';

export interface ChartBinding {
  source: 'SCENARIO' | 'SETUP' | 'ORDER' | 'DRAFT';
  id: string;
  instanceId: string | null;
  revision: number | null;
  symbol: string;
  direction: Direction | null;
  planFingerprint: string | null;
  snapshotId: string | null;
}

export interface EvidenceTime {
  occurredAtMs: number;
  knownAtMs: number;
  sourceTimeframe: string;
}

export interface ChartZoneV144 extends EvidenceTime {
  id: string;
  kind: 'FVG' | 'POI';
  top: number;
  bottom: number;
  startAtMs: number;
  endAtMs: number | null;
  direction: Direction | null;
  state: 'ACTIVE' | 'PARTIAL' | 'MITIGATED' | 'INVALIDATED';
  sourceIds: string[];
}

export function chartSelectionKey(binding: ChartBinding): string {
  return JSON.stringify([
    binding.source,
    binding.id,
    binding.instanceId,
    binding.revision,
    binding.symbol,
    binding.direction,
    binding.planFingerprint,
    binding.snapshotId,
  ]);
}

export function sameChartBinding(a: ChartBinding, b: ChartBinding): boolean {
  return chartSelectionKey(a) === chartSelectionKey(b);
}

export function validEvidenceTime(e: EvidenceTime, asOfMs: number): boolean {
  return (
    Number.isSafeInteger(e.occurredAtMs) &&
    e.occurredAtMs > 0 &&
    Number.isSafeInteger(e.knownAtMs) &&
    e.occurredAtMs <= e.knownAtMs &&
    e.knownAtMs <= asOfMs
  );
}

export interface SMCScenarioView {
  scenario_id: string;
  instance_id: string;
  revision: number;
  symbol: string;
  timeframe: string;
  strategy_family: string;
  direction: Direction | null;
  state: string;
  as_of_ms: number;
  summary_vi?: string;
  current_snapshot_id?: string;
  anchor_level_id?: string;
  target_level_id?: string;
  payload?: any;
}

export interface ChartEvidenceBundleV144 {
  binding: ChartBinding;
  as_of_ms: number;
  evidence_cutoff_ms: number;
  strategy_family: string;
  state: string;
  summary_vi?: string;
  structure_events: any[];
  poi_zones: any[];
  liquidity_levels: any[];
  displacement_annotation?: any;
  scenario_path: any[];
  risk_reward_plan?: any;
  invalidation_price?: number;
  warnings: string[];
  is_empty?: boolean;
}
