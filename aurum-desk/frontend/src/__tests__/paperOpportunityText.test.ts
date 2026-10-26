import { describe, it, expect } from 'vitest';
import {
  deriveBotWaitingSummary,
  formatMonitorTime,
  buildMonitorTimeWindow,
  validateMonitorChartFocusContext,
} from '../utils/paperOpportunityText';
import { deriveCG01DisplayState, normalizeCG01EvaluationPayload, type CG01DisplayState } from '../utils/cg01DisplayState';

describe('Aurum V17.0 — paperOpportunityText deriveBotWaitingSummary', () => {
  const baseDisplay: CG01DisplayState = deriveCG01DisplayState(
    normalizeCG01EvaluationPayload({
      stage: 'OBSERVING',
      enabled: true,
      evaluated: true,
      direction: 'LONG',
      poi_id: 'poi-1',
      as_of_ms: 1700000000000,
    }),
    true,
    1700000000000
  );

  it('reports disconnected state with danger tone', () => {
    const res = deriveBotWaitingSummary({ ...baseDisplay, isConnected: false });
    expect(res.headline).toBe('Mất kết nối');
    expect(res.tone).toBe('danger');
  });

  it('reports stale state with warning tone', () => {
    const res = deriveBotWaitingSummary({ ...baseDisplay, isStale: true });
    expect(res.headline).toBe('Dữ liệu chậm');
    expect(res.tone).toBe('warning');
  });

  it('reports model disabled state with neutral tone', () => {
    const res = deriveBotWaitingSummary({
      ...baseDisplay,
      stage: 'MODEL_DISABLED',
      primaryReason: 'MODEL_DISABLED',
    });
    expect(res.headline).toBe('CG-01 đang tắt');
    expect(res.tone).toBe('neutral');
  });

  it('reports armed state without claiming filled', () => {
    const res = deriveBotWaitingSummary({
      ...baseDisplay,
      stage: 'ARMED',
      primaryReason: 'ARMED',
      tone: 'success',
    });
    expect(res.headline).toBe('Đã đặt lệnh chờ (ARM)');
    expect(res.tone).toBe('success');
    expect(res.detail).toContain('chờ khớp');
    expect(res.detail).not.toContain('Đã khớp');
  });

  it('reports guard blocked state with warning tone and reason', () => {
    const res = deriveBotWaitingSummary({
      ...baseDisplay,
      stage: 'GUARD_BLOCKED',
      primaryReason: 'NEWS_BLACKOUT',
      tone: 'warning',
    });
    expect(res.headline).toBe('Tạm dừng vào lệnh');
    expect(res.tone).toBe('warning');
    expect(res.detail).toContain('tin tức');
  });

  it('reports wait retest state with info tone', () => {
    const res = deriveBotWaitingSummary({
      ...baseDisplay,
      stage: 'WAIT_RETEST',
      primaryReason: 'WAIT_RETEST',
    });
    expect(res.headline).toBe('Chờ giá hồi về (Retest)');
    expect(res.tone).toBe('info');
  });

  it('reports wait mss state with info tone', () => {
    const res = deriveBotWaitingSummary({
      ...baseDisplay,
      stage: 'WAIT_MSS',
      primaryReason: 'WAIT_MSS',
    });
    expect(res.headline).toBe('Chờ phá cấu trúc (MSS)');
    expect(res.tone).toBe('info');
  });

  it('reports default observing state accurately', () => {
    const res = deriveBotWaitingSummary(baseDisplay);
    expect(res.headline).toBe('Đang theo dõi');
    expect(res.detail).toBe(baseDisplay.description);
    expect(res.tone).toBe('neutral');
  });

  it('handles null/undefined input gracefully', () => {
    const res = deriveBotWaitingSummary(null);
    expect(res.headline).toBe('Đang khởi động');
    expect(res.tone).toBe('neutral');
  });

  it('formats monitor time correctly', () => {
    expect(formatMonitorTime(null)).toBe('--:--:--');
    expect(formatMonitorTime(0)).toBe('--:--:--');
    const formatted = formatMonitorTime(1700000000000);
    expect(formatted).toMatch(/\d{2}:\d{2}:\d{2}/);
  });
});

describe('Aurum V17.1 — buildMonitorTimeWindow', () => {
  it('computes today window in Asia/Ho_Chi_Minh with half-open bound', () => {
    // 2026-10-22 10:00:00 UTC = 2026-10-22 17:00:00 VN (+07:00)
    const fixedNowMs = Date.UTC(2026, 9, 22, 10, 0, 0);
    const win = buildMonitorTimeWindow({ period: 'today' }, 'Asia/Ho_Chi_Minh', fixedNowMs);
    expect(win.end_ms).toBe(fixedNowMs);
    expect(win.start_ms).toBeLessThan(win.end_ms!);
    // Start of day in UTC+7 for 2026-10-22 is 2026-10-21 17:00:00 UTC
    const expectedStartUtc = Date.UTC(2026, 9, 21, 17, 0, 0);
    expect(win.start_ms).toBe(expectedStartUtc);
  });

  it('computes rolling 7d window', () => {
    const fixedNowMs = 1700000000000;
    const win = buildMonitorTimeWindow({ period: '7d' }, 'Asia/Ho_Chi_Minh', fixedNowMs);
    expect(win.end_ms).toBe(fixedNowMs);
    expect(win.start_ms).toBe(fixedNowMs - 7 * 86400 * 1000);
  });

  it('computes custom date range with next-day exclusive boundary', () => {
    const win = buildMonitorTimeWindow(
      { period: 'custom', customStartDate: '2026-10-20', customEndDate: '2026-10-21' },
      'Asia/Ho_Chi_Minh'
    );
    expect(win.start_ms).toBe(Date.parse('2026-10-20T00:00:00+07:00'));
    expect(win.end_ms).toBe(Date.parse('2026-10-22T00:00:00+07:00'));
    expect(win.end_ms! - win.start_ms!).toBe(2 * 86400 * 1000);
  });
});

describe('Aurum V17.1 — validateMonitorChartFocusContext', () => {
  const validOrderPayload = {
    status: 'AVAILABLE' as const,
    kind: 'ORDER' as const,
    query_key: 'qk-1',
    opportunity_id: 'opp-1',
    target_id: 'ord-100',
    dto: {
      id: 'ord-100',
      order_id: 'ord-100',
      setup_id: 'setup-100',
      instrument: 'XAUUSDT',
      direction: 'LONG' as const,
      order_type: 'LIMIT',
      state: 'FILLED',
      planned_entry: 2650.0,
      actual_entry: 2650.0,
      stop_loss: 2640.0,
      take_profit: 2670.0,
      quantity: 0.1,
      cost_snapshot: {
        minimum_snapshot_rr: 2.0,
      },
    },
  };

  it('validates canonical AVAILABLE ORDER successfully', () => {
    const res = validateMonitorChartFocusContext(validOrderPayload, {
      expectedKind: 'ORDER',
      expectedTargetId: 'ord-100',
      expectedOpportunityId: 'opp-1',
      expectedSymbol: 'XAUUSDT',
    });
    expect(res.status).toBe('AVAILABLE');
    if (res.status === 'AVAILABLE') {
      expect(res.kind).toBe('ORDER');
      expect(res.dto.planned_entry).toBe(2650.0);
      expect(res.dto.stop_loss).toBe(2640.0);
    }
  });

  it('rejects order with invalid geometry (planned_entry === stop_loss)', () => {
    const malformed = {
      ...validOrderPayload,
      dto: { ...validOrderPayload.dto, stop_loss: 2650.0 },
    };
    const res = validateMonitorChartFocusContext(malformed);
    expect(res.status).toBe('UNAVAILABLE');
    if (res.status === 'UNAVAILABLE') {
      expect(res.reason_code).toBe('INVALID_GEOMETRY');
    }
  });

  it('rejects target when expected target id mismatches', () => {
    const res = validateMonitorChartFocusContext(validOrderPayload, {
      expectedTargetId: 'ord-999',
    });
    expect(res.status).toBe('SCOPE_MISMATCH');
    if (res.status === 'SCOPE_MISMATCH') {
      expect(res.reason_code).toBe('TARGET_MISMATCH');
    }
  });

  it('preserves UNAVAILABLE or STALE_TARGET response from backend', () => {
    const staleResp = {
      status: 'STALE_TARGET' as const,
      query_key: 'qk-1',
      opportunity_id: 'opp-1',
      reason_code: 'SETUP_REVISION_STALE',
      message_vi: 'Kế hoạch đã thay đổi phiên bản mới',
    };
    const res = validateMonitorChartFocusContext(staleResp);
    expect(res.status).toBe('STALE_TARGET');
    if (res.status === 'STALE_TARGET') {
      expect(res.reason_code).toBe('SETUP_REVISION_STALE');
      expect(res.message_vi).toContain('Kế hoạch đã thay đổi');
    }
  });
});
