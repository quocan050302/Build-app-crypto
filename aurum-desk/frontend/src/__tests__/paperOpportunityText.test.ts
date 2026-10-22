import { describe, it, expect } from 'vitest';
import { deriveBotWaitingSummary, formatMonitorTime } from '../utils/paperOpportunityText';
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
