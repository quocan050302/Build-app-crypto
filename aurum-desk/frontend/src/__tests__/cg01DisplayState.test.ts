/**
 * Comprehensive Unit Tests for CG-01 Display State and Reason Catalog (V16.29).
 * Tests all required stages, reason translations, null safety, direction validation,
 * POI compaction, and staleness detection.
 * Conforms to prompt rules 041-058, 066-086, 131-140.
 */

import { describe, it, expect } from 'vitest';
import {
  deriveCG01DisplayState,
  compactPoiId,
  normalizeCG01Direction,
  CG01_STALE_THRESHOLD_MS,
  type CG01EvaluationPayload,
} from '../utils/cg01DisplayState';
import {
  getCG01StageTitle,
  getCG01ReasonDescription,
} from '../utils/cg01ReasonText';

describe('CG-01 Reason Catalog & Text Formatter', () => {
  it('translates WAIT_SWEEP / WAITING_SWEEP_CONFIRMATION (Item 073)', () => {
    const text = getCG01ReasonDescription('WAITING_SWEEP_CONFIRMATION');
    expect(text).toContain('chờ giá quét mốc thanh khoản');
  });

  it('translates WAIT_MSS / WAITING_MSS_CONFIRMATION (Item 074, 133)', () => {
    const text = getCG01ReasonDescription('WAITING_MSS_CONFIRMATION');
    expect(text).toContain('phá cấu trúc');
    expect(text).toContain('lực đẩy');
  });

  it('translates WAIT_FVG / NO_FVG_ON_LEG (Item 075, 076, 134)', () => {
    const textFvg = getCG01ReasonDescription('WAIT_FVG');
    expect(textFvg).toContain('chờ FVG');

    const textNoFvg = getCG01ReasonDescription('NO_FVG_ON_LEG');
    expect(textNoFvg).toContain('chưa tạo được FVG');
  });

  it('translates FVG_USED_BEFORE_ARM without raw technical constant (Item 077, 135)', () => {
    const text = getCG01ReasonDescription('FVG_USED_BEFORE_ARM');
    expect(text).toContain('Vùng đã được chạm');
    expect(text).not.toContain('FVG_USED_BEFORE_ARM');
  });

  it('translates FVG_OVER_MITIGATED cleanly (Item 078, 086, 136)', () => {
    const text = getCG01ReasonDescription('FVG_OVER_MITIGATED');
    expect(text).toContain('quá sâu');
    expect(text).not.toContain('FVG_OVER_MITIGATED');
  });

  it('translates FVG_INVALIDATED (Item 079)', () => {
    const text = getCG01ReasonDescription('FVG_INVALIDATED');
    expect(text).toContain('vô hiệu hóa');
  });

  it('translates WAIT_RETEST / TRIGGER_EXPIRED / RR_BELOW_MINIMUM (Items 080-082)', () => {
    expect(getCG01ReasonDescription('WAIT_RETEST')).toContain('quay lại vùng');
    expect(getCG01ReasonDescription('TRIGGER_EXPIRED')).toContain('quá thời hạn');
    expect(getCG01ReasonDescription('RR_BELOW_MINIMUM')).toContain('chưa đạt ngưỡng');
  });

  it('translates TARGET_UNAVAILABLE & GEOMETRY_INVALID (Items 083-084)', () => {
    expect(getCG01ReasonDescription('TARGET_UNAVAILABLE')).toContain('Chưa xác định được mục tiêu');
    expect(getCG01ReasonDescription('GEOMETRY_INVALID')).toContain('Quan hệ entry/SL/TP');
  });

  it('handles unknown reason code cleanly without crash or [object Object] (Item 085, 137)', () => {
    const unknown = getCG01ReasonDescription('UNEXPECTED_NEW_CODE_XYZ');
    expect(unknown).toContain('Đang theo dõi điều kiện: UNEXPECTED_NEW_CODE_XYZ');
    expect(unknown).not.toContain('undefined');
    expect(unknown).not.toContain('null');
    expect(unknown).not.toContain('[object Object]');

    const nullReason = getCG01ReasonDescription(null);
    expect(nullReason).toContain('Đang theo dõi');
  });

  it('returns clean stage titles', () => {
    expect(getCG01StageTitle('MODEL_DISABLED')).toBe('Mô Hình Đang Tắt');
    expect(getCG01StageTitle('WAIT_MSS')).toBe('Chờ Phá Cấu Trúc (MSS)');
    expect(getCG01StageTitle('ARMED')).toBe('Đã Đặt Lệnh Chờ');
    expect(getCG01StageTitle('GUARD_BLOCKED')).toBe('Bị Chặn Bởi Guard');
    expect(getCG01StageTitle('DEGRADED')).toBe('Hệ Thống Tạm Dừng');
  });
});

describe('CG-01 Direction Normalizer & POI Compactor', () => {
  it('normalizes direction strictly and NEVER defaults to LONG (Item 047, 138)', () => {
    expect(normalizeCG01Direction('LONG')).toBe('LONG');
    expect(normalizeCG01Direction('SHORT')).toBe('SHORT');
    expect(normalizeCG01Direction(null)).toBe('CHƯA XÁC ĐỊNH');
    expect(normalizeCG01Direction(undefined)).toBe('CHƯA XÁC ĐỊNH');
    expect(normalizeCG01Direction('INVALID')).toBe('CHƯA XÁC ĐỊNH');
    expect(normalizeCG01Direction('')).toBe('CHƯA XÁC ĐỊNH');
  });

  it('compacts long POI ID safely (Item 113)', () => {
    expect(compactPoiId(null)).toBeNull();
    expect(compactPoiId('poi_short')).toBe('poi_short');
    const longPoi = 'poi_xauusdt_15m_demand_1729000000000';
    const compacted = compactPoiId(longPoi);
    expect(compacted).toBe('poi_xauu...000000');
    expect(compacted!.length).toBeLessThan(longPoi.length);
  });
});

describe('deriveCG01DisplayState', () => {
  const baseNow = 1_700_000_000_000;

  it('handles null payload gracefully (Item 050, 132)', () => {
    const state = deriveCG01DisplayState(null, true, baseNow);
    expect(state.title).toContain('Đang Khởi Động');
    expect(state.stage).toBe('OBSERVING');
    expect(state.direction).toBe('CHƯA XÁC ĐỊNH');
    expect(state.poiId).toBeNull();
    expect(state.tone).toBe('neutral');
  });

  it('handles MODEL_DISABLED (Item 056, 139)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: false,
      evaluated: false,
      stage: 'MODEL_DISABLED',
      reason_codes: ['MODEL_DISABLED'],
      as_of_ms: baseNow - 5000,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.stage).toBe('MODEL_DISABLED');
    expect(state.stageLabel).toBe('Mô Hình Đang Tắt');
    expect(state.tone).toBe('neutral');
    expect(state.description).toContain('tắt');
    expect(state.eligible).toBe(false);
  });

  it('handles NO_QUOTE (Item 057)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      evaluated: false,
      stage: 'NO_QUOTE',
      reason_codes: ['NO_QUOTE'],
      as_of_ms: baseNow - 1000,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.stage).toBe('NO_QUOTE');
    expect(state.stageLabel).toBe('Thiếu Báo Giá');
    expect(state.tone).toBe('warning');
    expect(state.description).toContain('Thiếu báo giá');
  });

  it('handles WARMUP_INSUFFICIENT and ATR_WARMUP (Item 058, 140)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      evaluated: false,
      stage: 'WARMUP_INSUFFICIENT',
      reason_codes: ['WARMUP_INSUFFICIENT'],
      as_of_ms: baseNow - 1000,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.stage).toBe('WARMUP_INSUFFICIENT');
    expect(state.description).toContain('thiếu dữ liệu');
    expect(state.tone).toBe('warning');
  });

  it('handles WAIT_MSS and WAIT_FVG stage progression (Item 141)', () => {
    // 1. WAIT_SWEEP
    const p1: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_SWEEP',
      direction: 'LONG',
      poi_id: 'poi_01',
      reason_codes: ['WAITING_SWEEP_CONFIRMATION'],
      as_of_ms: baseNow - 3000,
    };
    const s1 = deriveCG01DisplayState(p1, true, baseNow);
    expect(s1.stage).toBe('WAIT_SWEEP');
    expect(s1.direction).toBe('LONG');
    expect(s1.description).toContain('quét mốc thanh khoản');

    // 2. WAIT_MSS
    const p2: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_MSS',
      direction: 'LONG',
      poi_id: 'poi_01',
      reason_codes: ['WAITING_MSS_CONFIRMATION'],
      as_of_ms: baseNow - 2000,
    };
    const s2 = deriveCG01DisplayState(p2, true, baseNow);
    expect(s2.stage).toBe('WAIT_MSS');
    expect(s2.description).toContain('phá cấu trúc');

    // 3. WAIT_FVG
    const p3: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'WAIT_FVG',
      direction: 'LONG',
      poi_id: 'poi_01',
      reason_codes: ['WAIT_FVG'],
      as_of_ms: baseNow - 1000,
    };
    const s3 = deriveCG01DisplayState(p3, true, baseNow);
    expect(s3.stage).toBe('WAIT_FVG');
    expect(s3.description).toContain('chờ FVG');
  });

  it('handles ARMED lifecycle state (Item 052, 053, 142)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'ARMED',
      direction: 'SHORT',
      poi_id: 'poi_arm_01',
      armed_order_id: 'paper_order_999',
      eligible: true,
      reason_codes: ['EXECUTABLE'],
      as_of_ms: baseNow - 500,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.isArmed).toBe(true);
    expect(state.tone).toBe('success');
    expect(state.stageLabel).toBe('Đã Đặt Lệnh Chờ');
    // Does NOT claim filled
    expect(state.description).toContain('Đã đặt lệnh chờ');
    expect(state.description).toContain('paper_order_999');
    expect(state.description).not.toContain('Đã vào lệnh');
  });

  it('handles GUARD_BLOCKED lifecycle state (Item 054, 143)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'GUARD_BLOCKED',
      direction: 'LONG',
      poi_id: 'poi_blocked',
      guard_summary: {
        blocked_reason: 'DAILY_MAX_CAP_REACHED',
        blocked_at: baseNow - 200,
      },
      reason_codes: ['EXECUTABLE', 'DAILY_MAX_CAP_REACHED'],
      as_of_ms: baseNow - 200,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.isGuardBlocked).toBe(true);
    expect(state.tone).toBe('warning');
    expect(state.stageLabel).toBe('Bị Chặn Bởi Guard');
    expect(state.description).toContain('DAILY_MAX_CAP_REACHED');
  });

  it('handles DEGRADED lifecycle state on transaction rollback (Item 055, 144)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      evaluated: true,
      stage: 'DEGRADED',
      direction: 'LONG',
      poi_id: 'poi_degraded',
      arm_outcome: 'TRANSACTION_ROLLBACK',
      reason_codes: ['TRANSACTION_COMMIT_FAILED'],
      as_of_ms: baseNow - 100,
    };
    const state = deriveCG01DisplayState(payload, true, baseNow);
    expect(state.isDegraded).toBe(true);
    expect(state.tone).toBe('danger');
    expect(state.stageLabel).toBe('Hệ Thống Tạm Dừng');
    expect(state.description).toContain('lỗi ghi nhận dữ liệu');
  });

  it('tracks fresh vs stale updates based on 90s boundary (Item 145)', () => {
    const freshPayload: CG01EvaluationPayload = {
      enabled: true,
      as_of_ms: baseNow - (CG01_STALE_THRESHOLD_MS - 5000), // 85s ago -> Fresh
      stage: 'WAIT_MSS',
    };
    const freshState = deriveCG01DisplayState(freshPayload, true, baseNow);
    expect(freshState.isStale).toBe(false);
    expect(freshState.title).not.toContain('Chậm Cập Nhật');

    const stalePayload: CG01EvaluationPayload = {
      enabled: true,
      as_of_ms: baseNow - (CG01_STALE_THRESHOLD_MS + 5000), // 95s ago -> Stale
      stage: 'WAIT_MSS',
    };
    const staleState = deriveCG01DisplayState(stalePayload, true, baseNow);
    expect(staleState.isStale).toBe(true);
    expect(staleState.title).toContain('Chậm Cập Nhật');
  });

  it('handles disconnected state while preserving last known snapshot (Item 146)', () => {
    const payload: CG01EvaluationPayload = {
      enabled: true,
      stage: 'WAIT_FVG',
      direction: 'SHORT',
      poi_id: 'poi_short_01',
      as_of_ms: baseNow - 10000,
    };
    const state = deriveCG01DisplayState(payload, false, baseNow);
    expect(state.isConnected).toBe(false);
    expect(state.title).toContain('Mất Kết Nối');
    expect(state.description).toContain('Mất kết nối với máy chủ');
    // Retains last known snapshot metadata
    expect(state.poiId).toBe('poi_short_01');
    expect(state.direction).toBe('SHORT');
  });
});
