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
  kind: 'FVG' | 'POI' | 'OB';
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

export interface EntryExplanationNode {
  id?: string | null;
  status: 'CONFIRMED' | 'OBSERVED' | 'MISSING' | string;
  price?: number | null;
  occurred_at_ms?: number | null;
  known_at_ms?: number | null;
  source_ids?: string[];
  reason?: string;
  detail?: string;
}

export interface EntryExplanationDTO {
  context?: {
    h4_bias?: string;
    h1_alignment?: string;
    regime?: string;
  };
  tracked_levels?: Record<string, any>;
  nodes?: {
    sweep?: EntryExplanationNode;
    break?: EntryExplanationNode;
    fvg?: EntryExplanationNode;
    retest?: EntryExplanationNode;
  };
  evidence_summary?: Record<string, string>;
  target?: any;
  invalidation?: any;
  plan?: any;
  guard_audit?: any[];
}

export interface SnapshotProvenanceDTO {
  is_current: boolean | null;
  snapshot_detector_version?: string | null;
  current_detector_version?: string;
  snapshot_config_version?: number | null;
  current_config_version?: number;
  snapshot_requirement_profile?: string | null;
  current_requirement_profile?: string;
  historical_requirements?: Record<string, any> | null;
  current_requirements?: Record<string, any>;
  version_label?: string;
  warning?: string | null;
  // Aliases for backwards compatibility
  detector_version?: string | null;
  requirement_profile?: string | null;
  is_outdated_version?: boolean;
  is_custom_profile?: boolean;
}

export interface StructuralReferenceDTO {
  id?: string | null;
  price?: number | null;
  occurred_at_ms?: number | null;
  known_at_ms?: number | null;
  associated_event_id?: string | null;
}

export interface HtfStructureEventDTO {
  event_type: string;
  previous_phase?: string | null;
  new_phase: string;
  direction?: Direction | string | null;
  reference_id?: string | null;
  reference_price?: number | null;
  close_price?: number | null;
  occurred_at_ms?: number | null;
  known_at_ms: number;
}

export interface HtfSnapshotDTO {
  symbol: string;
  timeframe: string;
  as_of_ms: number;
  bias: string;
  phase: string;
  data_status: string;
  protected_low?: StructuralReferenceDTO | number | null;
  protected_high?: StructuralReferenceDTO | number | null;
  broken_protection?: StructuralReferenceDTO | number | null;
  latest_event?: HtfStructureEventDTO | null;
  invalidation_cutoff_ms?: number | null;
  effective_settings?: Record<string, any>;
  settings_fingerprint?: string | null;
  algorithm_version?: string;
}

export interface HtfContextDTO {
  schema_version?: string;
  evaluation_as_of_ms?: number;
  h4_snapshot?: HtfSnapshotDTO | null;
  h1_snapshot?: HtfSnapshotDTO | null;
  direction_context?: {
    relation?: string;
    executable_permission?: boolean;
    reason?: string;
    bias?: string;
    confidence_level?: string;
  } | null;
  effective_settings?: Record<string, any>;
  config_version?: number;
  settings_fingerprint?: string | null;
  context_source?: string;
}

/**
 * Rule 122: Extract valid finite price from StructuralReferenceDTO, number, or return null.
 * Strictly prevents parseFloat(String(object)) or Number(object) resulting in NaN.
 */
export function readStructuralReferencePrice(
  ref: StructuralReferenceDTO | number | null | undefined
): number | null {
  if (ref == null) return null;
  if (typeof ref === 'number') {
    return Number.isFinite(ref) ? ref : null;
  }
  if (typeof ref === 'object') {
    const price = ref.price;
    if (typeof price === 'number' && Number.isFinite(price)) {
      return price;
    }
  }
  return null;
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
  provenance?: SnapshotProvenanceDTO;
  entry_explanation?: EntryExplanationDTO;
  htf_context?: HtfContextDTO;
}
