import React from 'react';
import { api } from '../api/client';
import type { NYQuotaDiagnostics } from '../api/client';
import { blockerLabel, categoryLabel, countText, funnelLine, validateNyDate } from '../utils/nyQuotaLabels';

/**
 * V14.3 - compact, READ-ONLY diagnostics inside the existing session drawer.
 * Only GET requests; picking a date never touches orders / quota / notifications.
 */
export const NYQuotaDiagnosticsCard: React.FC<{ symbol?: string }> = ({ symbol = 'XAUUSDT' }) => {
  // null = "current NY session" as decided by the SERVER clock (the browser clock is never the authority).
  const [date, setDate] = React.useState<string | null>(null);
  const [serverToday, setServerToday] = React.useState<string | null>(null);
  const [data, setData] = React.useState<NYQuotaDiagnostics | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const invalid = date !== null && serverToday ? validateNyDate(date, serverToday) : null;
    if (invalid) {
      setError(invalid);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .getNYQuotaDiagnostics(symbol, date ?? undefined)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        if (d.is_today) setServerToday(d.session_date_ny);
      })
      .catch(() => {
        if (!cancelled) setError('Không tải được chẩn đoán phiên');
      });
    return () => {
      cancelled = true;
    };
    // serverToday only validates typed dates; it must not retrigger a fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, date]);

  return (
    <div data-testid="ny-diagnostics" className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-bold text-gray-200">Vì sao có / không có lệnh phiên Mỹ</span>
        <label className="flex items-center gap-1 text-[11px] text-gray-400">
          Ngày phiên NY
          <input
            data-testid="ny-diagnostics-date"
            type="date"
            value={date ?? data?.session_date_ny ?? ''}
            max={serverToday ?? undefined}
            onChange={(e) => setDate(e.target.value || null)}
            className="bg-charcoal-900 border border-charcoal-700 rounded px-1 py-0.5 text-gray-200"
          />
        </label>
      </div>
      {error && <div data-testid="ny-diagnostics-error" className="text-rose-300">{error}</div>}
      {!error && !data && <div className="text-gray-500">Đang tải…</div>}
      {!error && data && (
        <div className="space-y-1.5">
          <div data-testid="ny-diagnostics-funnel" className="text-gray-300">{funnelLine(data)}</div>
          <div className="text-gray-400">
            Lượt quét: <span className="font-mono text-gray-200">{countText(data.scans)}</span>
            {data.quota && (
              <>
                {' · '}Khớp thật:{' '}
                <span className="font-mono text-gray-200">{countText(data.quota.total_fills)}</span>/
                <span className="font-mono text-gray-200">{countText(data.quota.target)}</span>
              </>
            )}
          </div>
          {data.availability === 'MEASURED' && (data.filled ?? 0) === 0 && data.primary_missed_reason && (
            <div data-testid="ny-diagnostics-missed" className="text-amber-300">
              {categoryLabel(data.missed_category)}: {blockerLabel(data.primary_missed_reason, data.primary_missed_message_vi)}
            </div>
          )}
          {data.top_blockers.length > 0 && (
            <ul data-testid="ny-diagnostics-blockers" className="space-y-0.5">
              {data.top_blockers.map((b) => (
                <li key={b.code} className="flex justify-between gap-2 text-gray-400">
                  <span>
                    <span className="text-gray-500">[{categoryLabel(b.category)}]</span> {blockerLabel(b.code, b.message_vi)}
                  </span>
                  <span className="font-mono text-gray-300">{countText(b.count)}</span>
                </li>
              ))}
            </ul>
          )}
          {data.last_system_error && <div className="text-rose-300 font-mono text-[10px] break-all">{data.last_system_error}</div>}
        </div>
      )}
    </div>
  );
};
