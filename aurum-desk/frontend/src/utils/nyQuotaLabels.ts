/**
 * V14.3 - pure label helpers for the NY session bar and the diagnostics section.
 *
 * Rules (enforced by tests):
 *  - the "Mỹ n/target" counter is ONLY the actual PAPER fills reported by the backend; a pending/READY/ARMED
 *    setup never makes it look fulfilled;
 *  - SEEKING_FALLBACK is shown as "searching fallback" ONLY while the backend says the fallback flag is on;
 *  - null counts (not measured) are rendered as "—", never as 0.
 */
import type { NYSessionStatus, NYQuotaDiagnostics } from '../api/client';

export type NYSessionView = Pick<
  NYSessionStatus,
  'quota_state' | 'is_fallback_active' | 'ny_fills' | 'ny_min_fills'
> & Partial<Pick<NYSessionStatus, 'fallback_enabled' | 'quota_reason' | 'reason_message' | 'target_fills'>>;

export function isFallbackSearching(s: NYSessionView): boolean {
  return s.quota_state === 'SEEKING_FALLBACK' && s.is_fallback_active === true && s.fallback_enabled !== false;
}

export function isFallbackDisabledInWindow(s: NYSessionView): boolean {
  return s.fallback_enabled === false && (s.quota_state === 'SEEKING_STANDARD' || s.quota_state === 'SEEKING_FALLBACK');
}

export function nyCounterText(s: Pick<NYSessionView, 'ny_fills' | 'ny_min_fills'>): string {
  return `${s.ny_fills}/${s.ny_min_fills}`;
}

export function nyStatusLabel(s: NYSessionView): string {
  if (s.quota_state === 'SEEKING_FALLBACK') {
    return isFallbackSearching(s) ? 'ĐANG TÌM FALLBACK (10:30+)' : 'FALLBACK ĐANG TẮT';
  }
  if (s.quota_state === 'SEEKING_STANDARD') {
    return s.fallback_enabled === false ? 'ĐANG TÌM SMC · FALLBACK TẮT' : 'ĐANG TÌM SMC';
  }
  switch (s.quota_state) {
    case 'FULFILLED':
      return `ĐÃ HOÀN THÀNH CHỈ TIÊU (${nyCounterText(s)})`;
    case 'ARMED_PENDING_FILL':
      return 'ĐÃ LÊN NÒNG — CHỜ KHỚP';
    case 'MISSED':
      return 'KẾT THÚC CỬA SỔ — KHÔNG CÓ LỆNH';
    case 'NOT_STARTED':
      return 'CHƯA TỚI CỬA SỔ';
    default:
      return s.quota_state;
  }
}

/** One short reason line (the bar shows exactly one). Prefers the real backend reason. */
export function nyReasonLine(s: NYSessionView): string {
  if (isFallbackDisabledInWindow(s) && !s.quota_reason) return 'Fallback đang tắt — chỉ quét cơ hội SMC chuẩn';
  if (s.quota_reason) return s.quota_reason;
  if (s.quota_state === 'ARMED_PENDING_FILL') return 'Đã arm, chưa khớp — chỉ tiêu vẫn tính 0 cho tới khi khớp thật';
  return s.reason_message || '';
}

const BLOCKER_LABELS: Record<string, string> = {
  FALLBACK_DISABLED: 'Fallback đang tắt',
  STANDARD_DATA_MISSING: 'Thiếu dữ liệu 15M cho SMC chuẩn',
  NO_RETEST_YET: 'Chờ hồi về FVG',
  NO_FVG: 'Chưa có FVG hợp lệ',
  NO_TARGET_RR: 'Không có target đủ RR',
  FALLBACK_DATA_ERROR: 'Lỗi dữ liệu fallback',
  NEWS_BLACKOUT: 'Tạm dừng vì tin đỏ',
};

export function blockerLabel(code: string, messageVi?: string | null): string {
  return messageVi || BLOCKER_LABELS[code] || code;
}

const CATEGORY_LABELS: Record<string, string> = {
  DATA: 'Dữ liệu',
  STRATEGY: 'Chiến lược',
  SAFETY: 'An toàn',
  EXECUTION: 'Thực thi',
  SYSTEM: 'Hệ thống',
};

export function categoryLabel(cat?: string | null): string {
  return cat ? (CATEGORY_LABELS[cat] ?? cat) : '—';
}

/** null (not measured) renders "—", a real zero renders "0". */
export function countText(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : String(n);
}

export function funnelLine(d: Pick<NYQuotaDiagnostics, 'availability' | 'unique_candidates' | 'financially_eligible' | 'armed' | 'filled'>): string {
  if (d.availability === 'NOT_STARTED') return 'Phiên chưa bắt đầu';
  if (d.availability === 'UNAVAILABLE') return 'Chưa có dữ liệu đo cho phiên này';
  return `Tín hiệu ${countText(d.unique_candidates)} → đủ RR ${countText(d.financially_eligible)} → arm ${countText(d.armed)} → khớp ${countText(d.filled)}`;
}

/** YYYY-MM-DD validation for the NY session date filter (no future dates). */
export function validateNyDate(value: string, todayNy: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Ngày phải có dạng YYYY-MM-DD (ngày phiên New York)';
  const d = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value) return 'Ngày không hợp lệ';
  if (value > todayNy) return 'Không xem được ngày chưa diễn ra';
  return null;
}
