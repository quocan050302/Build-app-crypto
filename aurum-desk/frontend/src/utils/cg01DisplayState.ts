/**
 * Pure Display State Derivation for CG-01 Diagnostics (V16.29).
 * Transforms raw serialized evaluation + connection telemetry into ready-to-render view model.
 * Zero side effects, completely deterministic and testable.
 * Conforms to prompt rules 041-058, 066-070, 085, 089, 106-107, 113-114.
 */

import { getCG01StageTitle, getCG01ReasonDescription } from './cg01ReasonText';

export interface CG01EvaluationPayload {
  enabled?: boolean;
  evaluated?: boolean;
  as_of_ms?: number;
  stage?: string;
  eligible?: boolean;
  reason_codes?: string[];
  candidate_key?: string | null;
  episode_key?: string | null;
  router_outcome?: string | null;
  arm_outcome?: string | null;
  armed_order_id?: string | null;
  guard_summary?: {
    blocked_reason?: string;
    blocked_at?: number;
    [key: string]: any;
  } | null;
  ui_summary_vi?: string | null;
  poi_id?: string | null;
  direction?: string | null;
}

export type CG01Tone = 'neutral' | 'info' | 'warning' | 'success' | 'danger';

export interface CG01DisplayState {
  title: string;
  description: string;
  stage: string;
  stageLabel: string;
  tone: CG01Tone;
  direction: 'LONG' | 'SHORT' | 'CHƯA XÁC ĐỊNH';
  poiId: string | null;
  poiIdCompact: string | null;
  isStale: boolean;
  isConnected: boolean;
  asOfMs: number;
  primaryReason: string | null;
  allReasons: string[];
  armedOrderId: string | null;
  eligible: boolean;
  enabled: boolean;
  isArmed: boolean;
  isGuardBlocked: boolean;
  isDegraded: boolean;
}

/**
 * Compacts long POI ID (e.g. "poi_xauusdt_15m_demand_1729000000000" -> "poi_..._0000000")
 * to prevent layout overflow on narrow sidebars (Item 113).
 */
export function compactPoiId(poiId: string | null | undefined): string | null {
  if (!poiId || typeof poiId !== 'string') return null;
  const trimmed = poiId.trim();
  if (trimmed.length <= 16) return trimmed;
  return `${trimmed.slice(0, 8)}...${trimmed.slice(-6)}`;
}

/**
 * Validates and normalizes direction strictly (Item 047).
 * NEVER defaults to LONG if data is missing or invalid.
 */
export function normalizeCG01Direction(dir: string | null | undefined): 'LONG' | 'SHORT' | 'CHƯA XÁC ĐỊNH' {
  if (dir === 'LONG') return 'LONG';
  if (dir === 'SHORT') return 'SHORT';
  return 'CHƯA XÁC ĐỊNH';
}

/** Default freshness staleness threshold: 90 seconds (1M cadence = 60s + grace). */
export const CG01_STALE_THRESHOLD_MS = 90_000;

/**
 * Pure function: deriveCG01DisplayState
 * Transforms raw evaluation payload into UI display state.
 */
export function deriveCG01DisplayState(
  payload: CG01EvaluationPayload | null | undefined,
  isConnected: boolean = true,
  nowMs: number = Date.now()
): CG01DisplayState {
  // 1. Safe default fallback if payload is uninitialized
  if (!payload || typeof payload !== 'object') {
    return {
      title: isConnected ? 'CG-01 Đang Khởi Động' : 'Mất Kết Nối Máy Chủ',
      description: isConnected
        ? 'Chưa nhận được chu kỳ đánh giá đầu tiên từ máy chủ.'
        : 'Dữ liệu chưa được cập nhật do mất kết nối WebSocket.',
      stage: 'OBSERVING',
      stageLabel: 'Đang Quan Sát',
      tone: 'neutral',
      direction: 'CHƯA XÁC ĐỊNH',
      poiId: null,
      poiIdCompact: null,
      isStale: false,
      isConnected,
      asOfMs: 0,
      primaryReason: null,
      allReasons: [],
      armedOrderId: null,
      eligible: false,
      enabled: false,
      isArmed: false,
      isGuardBlocked: false,
      isDegraded: false,
    };
  }

  const enabled = Boolean(payload.enabled);
  const stage = payload.stage || (enabled ? 'OBSERVING' : 'MODEL_DISABLED');
  const asOfMs = Number(payload.as_of_ms) || 0;
  const allReasons = Array.isArray(payload.reason_codes) ? payload.reason_codes : [];
  const primaryReason = allReasons.length > 0 ? allReasons[0] : null;
  const rawDirection = payload.direction;
  const direction = normalizeCG01Direction(rawDirection);
  const poiId = payload.poi_id || null;
  const poiIdCompact = compactPoiId(poiId);
  const armedOrderId = payload.armed_order_id || null;
  const eligible = Boolean(payload.eligible);

  // 2. Staleness detection (Items 106-107)
  const isStale = asOfMs > 0 && nowMs - asOfMs > CG01_STALE_THRESHOLD_MS;

  // 3. Lifecycle state flags
  const isArmed = stage === 'ARMED' || Boolean(armedOrderId);
  const isGuardBlocked = stage === 'GUARD_BLOCKED';
  const isDegraded = stage === 'DEGRADED';

  // 4. Determine Tone
  let tone: CG01Tone = 'neutral';
  if (!enabled || stage === 'MODEL_DISABLED') {
    tone = 'neutral';
  } else if (isArmed) {
    tone = 'success';
  } else if (isGuardBlocked) {
    tone = 'warning';
  } else if (isDegraded || stage === 'CYCLE_FAILED' || stage === 'INVALIDATED' || stage === 'EXPIRED') {
    tone = 'danger';
  } else if (stage === 'NO_QUOTE' || stage === 'WARMUP_INSUFFICIENT' || stage === 'ATR_WARMUP') {
    tone = 'warning';
  } else if (stage === 'EXECUTABLE') {
    tone = 'info';
  } else if (
    stage.startsWith('WAIT_') ||
    stage.startsWith('WAITING_')
  ) {
    tone = 'info';
  }

  // 5. Stage Label & Title
  const stageLabel = getCG01StageTitle(stage);
  let title = `CG-01: ${stageLabel}`;
  if (!isConnected) {
    title = `CG-01: Mất Kết Nối`;
  } else if (isStale) {
    title = `CG-01: ${stageLabel} (Chậm Cập Nhật)`;
  }

  // 6. Descriptive Explanation
  let description = '';
  if (!isConnected) {
    description = 'Mất kết nối với máy chủ - dữ liệu chưa được cập nhật.';
  } else if (isArmed) {
    description = armedOrderId
      ? `Đã đặt lệnh chờ (${armedOrderId}) theo kế hoạch.`
      : 'Đã đặt lệnh chờ thành công theo kế hoạch.';
  } else if (isGuardBlocked) {
    const blockedReason = payload.guard_summary?.blocked_reason || primaryReason || 'GUARD_BLOCKED';
    description = `Lệnh bị chặn bởi cơ chế kiểm soát rủi ro (${blockedReason}).`;
  } else if (isDegraded) {
    description = 'Hệ thống tạm dừng tạo lệnh do lỗi ghi nhận dữ liệu; các lệnh đã có vẫn an toàn.';
  } else {
    description = getCG01ReasonDescription(primaryReason, payload.ui_summary_vi);
  }

  return {
    title,
    description,
    stage,
    stageLabel,
    tone,
    direction,
    poiId,
    poiIdCompact,
    isStale,
    isConnected,
    asOfMs,
    primaryReason,
    allReasons,
    armedOrderId,
    eligible,
    enabled,
    isArmed,
    isGuardBlocked,
    isDegraded,
  };
}
