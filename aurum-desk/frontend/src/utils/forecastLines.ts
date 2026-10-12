import type { SessionForecastView } from '../types/sessionForecast';

export interface ForecastLine {
  label: string;
  text: string;
}

const clip = (s: string, n = 140): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function price(p: unknown): string {
  return typeof p === 'number' && Number.isFinite(p) ? p.toFixed(2) : 'n/a';
}

export function formatAsOf(ms: number | null | undefined): string {
  if (!ms || !Number.isSafeInteger(ms)) return 'n/a';
  return new Date(ms).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function dataStatusLabel(f: SessionForecastView): string | null {
  switch (f.data_status) {
    case 'PARTIAL':
      return 'Một phần';
    case 'NOT_STARTED':
      return 'Chưa bắt đầu';
    case 'DATA_UNAVAILABLE':
      return 'Thiếu dữ liệu';
    default:
      return f.is_partial ? 'Một phần' : null;
  }
}

/**
 * 4–6 compact lines for the sidebar. Nothing is invented: every line is a stored fact or an explicit
 * "chưa xác định". A null direction is shown as QUAN SÁT, never defaulted to LONG/SHORT.
 */
export function buildForecastLines(f: SessionForecastView): ForecastLine[] {
  const lines: ForecastLine[] = [];
  lines.push({ label: 'Bối cảnh', text: clip(f.facts[0] ?? f.summary_vi ?? 'Chưa có dữ kiện') });

  const watched = f.watched_liquidity.slice(0, 2).map((l) => `${l.label_vi} ${price(l.price)}`);
  lines.push({ label: 'Đang theo dõi', text: watched.length ? watched.join(' · ') : 'Chưa có mức thanh khoản có nguồn' });

  if (f.primary) {
    const dir = f.primary.direction ?? 'QUAN SÁT';
    lines.push({ label: `Giả thuyết (${dir})`, text: clip(f.primary.thesis_vi) });
  } else {
    lines.push({ label: 'Giả thuyết', text: 'QUAN SÁT — chưa đủ dữ kiện để nêu giả thuyết' });
  }

  lines.push({ label: 'Cần chờ', text: clip(f.confirmation[0] ?? 'Chưa có điều kiện xác nhận') });

  const target = f.target && typeof f.target.price === 'number'
    ? `${f.target.label_vi ?? 'Mục tiêu'} ${price(f.target.price)}`
    : 'Mục tiêu chưa xác định';
  const inval = f.invalidation[0] ?? f.primary?.invalidation_vi ?? 'chưa xác định';
  lines.push({ label: 'Mục tiêu / Hủy', text: clip(`${target} · Hủy nếu: ${inval}`) });

  lines.push({ label: 'As-of', text: formatAsOf(f.as_of_ms) });
  return lines;
}
