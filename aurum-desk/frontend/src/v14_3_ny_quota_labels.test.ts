import { describe, expect, it } from 'vitest';
import {
  blockerLabel, categoryLabel, countText, funnelLine, isFallbackSearching, nyCounterText, nyReasonLine,
  nyStatusLabel, validateNyDate,
} from './utils/nyQuotaLabels';

const base = { quota_state: 'SEEKING_FALLBACK', is_fallback_active: true, ny_fills: 0, ny_min_fills: 1, fallback_enabled: true };

describe('V14.3 NY quota labels', () => {
  it('shows fallback searching only while the flag is on', () => {
    expect(isFallbackSearching(base)).toBe(true);
    expect(nyStatusLabel(base)).toContain('ĐANG TÌM FALLBACK');
    const off = { ...base, fallback_enabled: false };
    expect(isFallbackSearching(off)).toBe(false);
    expect(nyStatusLabel(off)).not.toContain('ĐANG TÌM FALLBACK');
    expect(nyReasonLine(off)).toBe('Fallback đang tắt — chỉ quét cơ hội SMC chuẩn');
  });

  it('standard seeking with fallback disabled says so, enabled says "Đang tìm SMC"', () => {
    expect(nyStatusLabel({ ...base, quota_state: 'SEEKING_STANDARD' })).toBe('ĐANG TÌM SMC');
    expect(nyStatusLabel({ ...base, quota_state: 'SEEKING_STANDARD', fallback_enabled: false })).toContain('FALLBACK TẮT');
  });

  it('counter is actual fills only: pending/armed stays 0/1, fulfilled is 1/1', () => {
    const armed = { ...base, quota_state: 'ARMED_PENDING_FILL' };
    expect(nyCounterText(armed)).toBe('0/1');
    expect(nyStatusLabel(armed)).toBe('ĐÃ LÊN NÒNG — CHỜ KHỚP');
    const done = { ...base, quota_state: 'FULFILLED', ny_fills: 1 };
    expect(nyCounterText(done)).toBe('1/1');
    expect(nyStatusLabel(done)).toContain('(1/1)');
  });

  it('MISSED is not presented as an order and prefers the real backend reason', () => {
    const missed = { ...base, quota_state: 'MISSED', quota_reason: 'Hết cửa sổ — Dữ liệu: thiếu nến 5M' };
    expect(nyStatusLabel(missed)).toContain('KHÔNG CÓ LỆNH');
    expect(nyReasonLine(missed)).toBe('Hết cửa sổ — Dữ liệu: thiếu nến 5M');
  });

  it('null counts render as a dash, never as zero', () => {
    expect(countText(null)).toBe('—');
    expect(countText(undefined)).toBe('—');
    expect(countText(0)).toBe('0');
    expect(funnelLine({ availability: 'UNAVAILABLE', unique_candidates: null, financially_eligible: null, armed: null, filled: null }))
      .toBe('Chưa có dữ liệu đo cho phiên này');
    expect(funnelLine({ availability: 'MEASURED', unique_candidates: 2, financially_eligible: 1, armed: 1, filled: 0 }))
      .toBe('Tín hiệu 2 → đủ RR 1 → arm 1 → khớp 0');
  });

  it('labels blockers/categories in Vietnamese and falls back to code', () => {
    expect(blockerLabel('NO_FVG')).toBe('Chưa có FVG hợp lệ');
    expect(blockerLabel('X_UNKNOWN')).toBe('X_UNKNOWN');
    expect(blockerLabel('NO_FVG', 'Tin nhắn backend')).toBe('Tin nhắn backend');
    expect(categoryLabel('SAFETY')).toBe('An toàn');
    expect(categoryLabel(null)).toBe('—');
  });

  it('validates the NY date filter (format, real calendar day, not in the future)', () => {
    expect(validateNyDate('2026-10-09', '2026-10-11')).toBeNull();
    expect(validateNyDate('2026-10-12', '2026-10-11')).toContain('chưa diễn ra');
    expect(validateNyDate('2026-02-30', '2026-10-11')).toBe('Ngày không hợp lệ');
    expect(validateNyDate('09/10/2026', '2026-10-11')).toContain('YYYY-MM-DD');
  });
});
